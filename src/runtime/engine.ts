import { callDependency, DependencyTimeout } from "./dependencies.ts";
import { randomUUID } from "node:crypto";
import type {
  Candidate,
  Reservation,
  RuntimeConfig,
  RunState,
  Json,
  ToolAction,
  ToolReceipt,
} from "../contracts/index.ts";
import { money } from "../contracts/index.ts";
import type { Services } from "../modules/registry.ts";
import { Store } from "./store.ts";
import type { ActionRow, Lease } from "./store.ts";
function validateCandidate(value: Candidate) {
  if (
    !value ||
    typeof value.workKey !== "string" ||
    !value.workKey.length ||
    value.workKey.length > 200 ||
    typeof value.tool !== "string" ||
    !/^[a-z][a-z0-9_.-]{0,99}$/.test(value.tool)
  )
    throw new Error("Invalid business candidate");
  validateLimit(value.limit);
  if (Buffer.byteLength(JSON.stringify(value.input)) > 65536)
    throw new Error("Action input exceeds limit");
}
function validateLimit(limit: Candidate["limit"]) {
  if (!limit || limit.maximum.currency !== "USD")
    throw new Error("Unsupported currency");
  money(limit.maximum.micros);
  if (
    !Number.isSafeInteger(limit.maxDurationMs) ||
    limit.maxDurationMs < 1 ||
    limit.maxDurationMs > 300000 ||
    !Number.isSafeInteger(limit.maxOutputBytes) ||
    limit.maxOutputBytes < 1 ||
    limit.maxOutputBytes > 1048576 ||
    (limit.maxTokens !== undefined &&
      (!Number.isSafeInteger(limit.maxTokens) || limit.maxTokens < 1))
  )
    throw new Error("Invalid enforceable execution limits");
}
function validateReservation(value: Reservation, action: ToolAction) {
  validateLimit(value.limit);
  if (
    value.actionId !== action.actionId ||
    value.runId !== action.runId ||
    !value.reservationId ||
    !value.authorization ||
    value.limit.maximum.currency !== action.limit.maximum.currency ||
    value.limit.maximum.micros !== action.limit.maximum.micros ||
    value.limit.maxDurationMs !== action.limit.maxDurationMs ||
    value.limit.maxOutputBytes !== action.limit.maxOutputBytes ||
    value.limit.maxTokens !== action.limit.maxTokens
  )
    throw new Error(
      "Authorization does not match the requested action and limits",
    );
}
function validateReceipt(receipt: ToolReceipt, action: ToolAction) {
  if (
    receipt.version !== 1 ||
    receipt.actionId !== action.actionId ||
    !["succeeded", "failed"].includes(receipt.status) ||
    receipt.usage.actualCharge.currency !== "USD"
  )
    throw new Error("Invalid tool receipt");
  money(receipt.usage.actualCharge.micros);
  for (const value of [
    receipt.usage.provider,
    receipt.usage.accountRef,
    receipt.usage.externalEventId,
    receipt.usage.evidenceRef,
  ])
    if (typeof value !== "string" || !value || value.length > 2000)
      throw new Error("Receipt lacks charge/outcome evidence");
  if (
    typeof receipt.usage.billableFailure !== "boolean" ||
    typeof receipt.usage.provisional !== "boolean"
  )
    throw new Error("Receipt lacks accounting flags");
}
function boundedResult(value: unknown, maximum: number) {
  let bytes = 0;
  let reason: "output_limit" | "invalid_result" = "invalid_result";
  const active = new WeakSet<object>();
  const consume = (amount: number) => {
    bytes += amount;
    if (bytes > maximum) {
      reason = "output_limit";
      throw new Error("Output limit");
    }
  };
  const string = (text: string) => {
    consume(2);
    for (let i = 0; i < text.length; i++) {
      const point = text.charCodeAt(i);
      if (point === 34 || point === 92) consume(2);
      else if (point < 32) consume([8, 9, 10, 12, 13].includes(point) ? 2 : 6);
      else if (point < 128) consume(1);
      else if (point < 2048) consume(2);
      else if (
        point >= 0xd800 &&
        point <= 0xdbff &&
        text.charCodeAt(i + 1) >= 0xdc00 &&
        text.charCodeAt(i + 1) <= 0xdfff
      ) {
        consume(4);
        i++;
      } else if (point >= 0xd800 && point <= 0xdfff) consume(6);
      else consume(3);
    }
  };
  const visit = (item: unknown, depth: number): Json => {
    if (depth > 128) throw new Error("Output nesting");
    if (item === null) {
      consume(4);
      return null;
    }
    if (typeof item === "string") {
      string(item);
      return item;
    }
    if (typeof item === "boolean") {
      consume(item ? 4 : 5);
      return item;
    }
    if (typeof item === "number" && Number.isFinite(item)) {
      consume(JSON.stringify(item).length);
      return item;
    }
    if (typeof item !== "object" || active.has(item))
      throw new Error("Invalid JSON output");
    active.add(item);
    try {
      consume(2);
      if (Array.isArray(item)) {
        const copy: Json[] = [];
        for (let i = 0; i < item.length; i++) {
          if (i) consume(1);
          copy.push(visit(item[i], depth + 1));
        }
        return copy;
      }
      const prototype = Object.getPrototypeOf(item);
      if (prototype !== Object.prototype && prototype !== null)
        throw new Error("Output must be plain JSON");
      const copy: Record<string, Json> = {};
      let first = true;
      for (const key in item) {
        if (!Object.hasOwn(item, key)) continue;
        if (!first) consume(1);
        first = false;
        string(key);
        consume(1);
        Object.defineProperty(copy, key, {
          value: visit((item as Record<string, unknown>)[key], depth + 1),
          enumerable: true,
          configurable: true,
          writable: true,
        });
      }
      return copy;
    } finally {
      active.delete(item);
    }
  };
  try {
    return { result: visit(value, 0), rejected: false };
  } catch {
    return { result: { omitted: true, reason }, rejected: true };
  }
}
function prepareReceipt(receipt: ToolReceipt, action: ToolAction) {
  validateReceipt(receipt, action);
  const output = boundedResult(receipt.result, action.limit.maxOutputBytes);
  const normalized: ToolReceipt = {
    version: 1,
    actionId: receipt.actionId,
    status: receipt.status,
    result: output.result,
    usage: {
      actualCharge: money(receipt.usage.actualCharge.micros),
      provider: receipt.usage.provider,
      accountRef: receipt.usage.accountRef,
      externalEventId: receipt.usage.externalEventId,
      billableFailure: receipt.usage.billableFailure,
      provisional: receipt.usage.provisional,
      evidenceRef: receipt.usage.evidenceRef,
    },
  };
  let accountingMetadataOmitted = false;
  try {
    if (receipt.usage.nativeAmount !== undefined) {
      const native = receipt.usage.nativeAmount;
      if (
        native &&
        [
          native.currency,
          native.minorUnits,
          native.conversionRef,
          native.rounding,
        ].every(
          (value) =>
            typeof value === "string" &&
            value.length > 0 &&
            value.length <= 2000,
        )
      ) {
        const projected = {
          currency: native.currency,
          minorUnits: native.minorUnits,
          conversionRef: native.conversionRef,
          rounding: native.rounding,
        };
        if (!boundedResult(projected, 8192).rejected)
          normalized.usage.nativeAmount = projected;
        else accountingMetadataOmitted = true;
      } else accountingMetadataOmitted = true;
    }
  } catch {
    accountingMetadataOmitted = true;
  }
  try {
    if (
      typeof receipt.externalRef === "string" &&
      receipt.externalRef.length <= 2000
    )
      normalized.externalRef = receipt.externalRef;
  } catch {
    accountingMetadataOmitted = true;
  }
  return {
    receipt: normalized,
    outputRejected: output.rejected,
    accountingMetadataOmitted,
  };
}
export class AgentRuntime {
  readonly owner = randomUUID();
  private ticking = false;
  private dependency<T>(stage: string, call: () => Promise<T>) {
    return callDependency(stage, call, this.config.dependencyMs ?? 5000);
  }
  private activeController: AbortController | null = null;
  cancelActive() {
    this.activeController?.abort();
  }
  readonly store: Store;
  readonly services: Services;
  readonly config: RuntimeConfig;
  constructor(store: Store, services: Services, config: RuntimeConfig) {
    this.store = store;
    this.services = services;
    this.config = config;
  }
  async tick(): Promise<boolean> {
    if (this.ticking) return false;
    this.ticking = true;
    try {
      return await this.tickOwned();
    } finally {
      this.ticking = false;
    }
  }
  private async tickOwned(): Promise<boolean> {
    const lease = this.store.acquire(
      this.owner,
      Date.now(),
      this.config.leaseMs,
    );
    if (!lease) return false;
    const run = this.store.run(lease.runId)!;
    const abort = new AbortController();
    this.activeController = abort;
    let leaseLost = false;
    const heartbeat = setInterval(
      () => {
        try {
          if (!this.store.renew(lease, Date.now(), this.config.leaseMs)) {
            leaseLost = true;
            abort.abort();
          }
          if (this.store.run(run.id)?.desired === "stop") abort.abort();
        } catch {
          leaseLost = true;
          abort.abort();
        }
      },
      Math.max(100, Math.floor(this.config.leaseMs / 3)),
    );
    let acknowledge: string[] = [];
    let hasMoreEvents = false;
    const finish = (state: RunState, reason: string, wake: number | null) =>
      this.store.finish(
        lease,
        state,
        reason,
        hasMoreEvents && acknowledge.length > 0 ? Date.now() : wake,
        run.revision,
        acknowledge,
      );
    const retry = () => Date.now() + this.config.idleMs;
    try {
      if (run.profile !== this.config.profile) {
        finish(
          "failed",
          "Worker profile differs from durable run profile; start the correct worker",
          null,
        );
        return true;
      }
      if (this.services.tools.refresh)
        await this.dependency("tools.capabilities", () =>
          this.services.tools.refresh!(),
        );
      // Recover/settle recorded effects even while paused, stopped or exhausted.
      const outstanding = this.store
        .actions(run.id)
        .find((row) => !["completed", "failed"].includes(row.state));
      if (outstanding) {
        const state = await this.advance(lease, outstanding, abort.signal);
        const current = this.store.run(run.id)!;
        if (current.desired !== "run")
          finish(
            current.desired === "stop" ? "stopped" : "paused",
            "Operator control retained; reconciliation remains available",
            state === "done" ? null : retry(),
          );
        else
          finish(
            state === "exhausted"
              ? "exhausted"
              : state === "done"
                ? "active"
                : "waiting",
            state === "done"
              ? "Recorded outcome and settled usage"
              : "Awaiting dependency or outcome reconciliation",
            state === "done" ? Date.now() : retry(),
          );
        return true;
      }
      if (run.desired !== "run") {
        finish(
          run.desired === "stop" ? "stopped" : "paused",
          "No new work while operator control is set",
          null,
        );
        return true;
      }
      const financial = await this.dependency("economy.summary", () =>
        this.services.economy.summary(run.id),
      );
      if (
        run.state === "exhausted" &&
        (!financial || BigInt(financial.spendableMicros) <= 0n)
      ) {
        finish(
          "exhausted",
          "Awaiting verified available credit or operator recovery",
          retry(),
        );
        return true;
      }
      const pendingEvents = this.store.pendingEvents(run.id, 101);
      hasMoreEvents = pendingEvents.length > 100;
      const events = pendingEvents.slice(0, 100);
      const selected = await this.dependency("business.select", () =>
        this.services.business.selectWork({
          runId: run.id,
          now: new Date().toISOString(),
          financial,
          actions: this.store.views(run.id),
          capabilities: this.services.tools.capabilities(),
          events,
        }),
      );
      acknowledge = selected.acknowledgeEventIds ?? [];
      if (
        !Array.isArray(acknowledge) ||
        acknowledge.some((id) => !events.some((event) => event.eventId === id))
      )
        throw new Error(
          "Cannot acknowledge an event outside the inspected snapshot",
        );
      if (selected.kind !== "work") {
        let wake =
          selected.kind === "wait" && selected.wakeAt
            ? Date.parse(selected.wakeAt)
            : selected.kind === "unavailable"
              ? retry()
              : null;
        if (wake !== null && !Number.isFinite(wake))
          throw new Error(
            "Business wakeup must be a valid future UTC timestamp",
          );
        if (wake !== null && wake <= Date.now())
          wake = Date.now() + Math.min(1000, this.config.idleMs);
        finish("waiting", selected.reason, wake);
        return true;
      }
      validateCandidate(selected.candidate);
      if (
        !this.store.owned(lease) ||
        this.store.run(run.id)?.desired !== "run"
      ) {
        finish(
          "paused",
          "Operator changed control before planning",
          Date.now(),
        );
        return true;
      }
      const candidate = selected.candidate;
      const action: ToolAction = {
        ...candidate,
        version: 1,
        actionId: randomUUID(),
        runId: run.id,
      };
      const row = this.store.plan(lease, action, acknowledge, run.revision);
      if (!row) {
        acknowledge = [];
        finish(
          "waiting",
          "Run changed during selection; inspect the refreshed snapshot",
          Date.now(),
        );
        return true;
      }
      if (["completed", "failed"].includes(row.state)) {
        finish(
          "waiting",
          "Business selected an already finished work key; awaiting a new intent",
          null,
        );
        return true;
      }
      const result = await this.advance(lease, row, abort.signal);
      finish(
        result === "done"
          ? "active"
          : result === "exhausted"
            ? "exhausted"
            : "waiting",
        result === "done"
          ? "Action recorded"
          : "Awaiting authorization, dependency or reconciliation",
        result === "done" ? Date.now() : retry(),
      );
    } catch (error) {
      acknowledge = [];
      const timedOut = error instanceof DependencyTimeout;
      if (!leaseLost && this.store.owned(lease)) {
        this.store.fenced(lease, () => {
          this.store.db
            .prepare(
              "UPDATE runtime_actions SET state='unresolved',reason='Interrupted runtime operation; reconcile before execution',updated_at=? WHERE run_id=? AND state IN ('running','reserved')",
            )
            .run(Date.now(), run.id);
          this.store.db
            .prepare(
              "UPDATE runtime_outbox SET state='unresolved',updated_at=? WHERE action_id IN (SELECT id FROM runtime_actions WHERE run_id=? AND state='unresolved')",
            )
            .run(Date.now(), run.id);
          this.store.db
            .prepare("UPDATE runtime_runs SET last_error=? WHERE id=?")
            .run(
              timedOut
                ? "runtime.dependency_timeout"
                : "runtime.operation_failed",
              run.id,
            );
          this.store.event(
            run.id,
            timedOut ? "runtime.dependency_timeout" : "runtime.failed",
            timedOut
              ? `Free dependency timed out: ${error.stage}`
              : "Runtime operation failed; private action history retained",
          );
        });
        finish(
          timedOut ? "waiting" : "failed",
          timedOut
            ? "Dependency timed out; retry scheduled without repeating external effects"
            : "Runtime failure; operator may resume recovery",
          timedOut ? retry() : null,
        );
      }
    } finally {
      clearInterval(heartbeat);
      this.activeController = null;
    }
    return true;
  }
  private async advance(
    lease: Lease,
    row: ActionRow,
    signal: AbortSignal,
  ): Promise<"done" | "waiting" | "exhausted"> {
    const action = JSON.parse(row.action_json) as ToolAction;
    if (action.version !== 1)
      throw new Error("Unsupported saved action version");
    validateCandidate(action);
    let reservation = row.reservation_json
      ? (JSON.parse(row.reservation_json) as Reservation)
      : null;
    let justAuthorized = false;
    let receipt = row.receipt_json
      ? (JSON.parse(row.receipt_json) as ToolReceipt)
      : null;
    const canStart = () =>
      this.store.owned(lease) &&
      this.store.run(lease.runId)?.desired === "run" &&
      !signal.aborted;
    if (!reservation) {
      if (!canStart()) return "waiting";
      const capability = this.services.tools
        .capabilities()
        .find((item) => item.id === action.tool);
      if (!capability?.availability.available) return "waiting";
      const authorization = await this.dependency("economy.authorize", () =>
        this.services.economy.authorize({
          runId: action.runId,
          actionId: action.actionId,
          tool: action.tool,
          limit: action.limit,
        }),
      );
      if (authorization.kind !== "authorized") {
        this.store.fenced(lease, () =>
          this.store.db
            .prepare(
              "UPDATE runtime_actions SET reason=?,updated_at=? WHERE id=?",
            )
            .run(
              authorization.reason.slice(0, 2000),
              Date.now(),
              action.actionId,
            ),
        );
        return authorization.kind === "insufficient" ? "exhausted" : "waiting";
      }
      reservation = authorization.reservation;
      validateReservation(reservation, action);
      justAuthorized = true;
      this.store.fenced(lease, () =>
        this.store.db
          .prepare(
            "UPDATE runtime_actions SET state='reserved',reservation_json=?,updated_at=? WHERE id=?",
          )
          .run(JSON.stringify(reservation), Date.now(), action.actionId),
      );
    }
    validateReservation(reservation, action);
    const pendingState = (): "waiting" | "exhausted" =>
      receipt &&
      BigInt(receipt.usage.actualCharge.micros) >
        BigInt(reservation!.limit.maximum.micros)
        ? "exhausted"
        : "waiting";
    if (
      (!receipt || receipt.usage.provisional) &&
      (row.state === "running" || row.state === "unresolved")
    ) {
      const reconciled = await this.dependency("tools.reconcile", () =>
        this.services.tools.reconcile(action, reservation!),
      );
      if (reconciled.kind === "receipt") receipt = reconciled.receipt;
      else if (reconciled.kind === "not_performed") {
        if (receipt?.usage.provisional) {
          // The previous receipt already reported outcome/usage. An observation
          // without a final cost cannot authorize replay or erase that liability.
          this.unresolved(
            lease,
            action.actionId,
            "Provisional usage requires a final receipt before any new attempt",
          );
          return pendingState();
        }
        if (!reconciled.evidenceRef) return "waiting";
        if (this.services.economy.confirmNotPerformed) {
          const confirmation = await this.dependency(
            "economy.confirmNotPerformed",
            () =>
              this.services.economy.confirmNotPerformed!(
                reservation!,
                reconciled.evidenceRef,
              ),
          );
          if (confirmation.kind === "closed") {
            this.store.fenced(lease, () => {
              this.store.db
                .prepare(
                  "UPDATE runtime_actions SET state='failed',reason='Confirmed no effect; execution attempt ceiling reached',updated_at=? WHERE id=?",
                )
                .run(Date.now(), action.actionId);
              this.store.db
                .prepare(
                  "UPDATE runtime_outbox SET state='delivered',updated_at=? WHERE action_id=?",
                )
                .run(Date.now(), action.actionId);
              this.store.event(
                action.runId,
                "action.not_performed_closed",
                action.actionId,
              );
            });
            return "done";
          }
          if (confirmation.kind !== "renewed") {
            this.unresolved(lease, action.actionId, confirmation.reason);
            return "waiting";
          }
          if (!canStart()) return "waiting";
        }
        if (row.attempts >= 3 || !canStart()) return "waiting";
        this.store.fenced(lease, () => {
          this.store.db
            .prepare(
              "UPDATE runtime_actions SET state='reserved',reason=?,updated_at=? WHERE id=?",
            )
            .run(
              `Confirmed not performed: ${reconciled.evidenceRef}`.slice(
                0,
                2000,
              ),
              Date.now(),
              action.actionId,
            );
          this.store.db
            .prepare(
              "UPDATE runtime_outbox SET state='pending',updated_at=? WHERE action_id=?",
            )
            .run(Date.now(), action.actionId);
        });
      } else {
        this.unresolved(lease, action.actionId, reconciled.reason);
        return pendingState();
      }
    }
    if (!receipt) {
      if (!canStart()) return "waiting";
      if (
        !this.services.tools
          .capabilities()
          .some(
            (item) => item.id === action.tool && item.availability.available,
          )
      )
        return "waiting";
      if (!justAuthorized) {
        const renewed = await this.dependency("economy.authorize", () =>
          this.services.economy.authorize({
            runId: action.runId,
            actionId: action.actionId,
            tool: action.tool,
            limit: action.limit,
          }),
        );
        if (renewed.kind !== "authorized")
          return renewed.kind === "insufficient" ? "exhausted" : "waiting";
        validateReservation(renewed.reservation, action);
        if (renewed.reservation.reservationId !== reservation.reservationId)
          throw new Error("Reservation identity changed during recovery");
        reservation = renewed.reservation;
      }
      if (!canStart()) return "waiting";
      if (row.attempts >= 3) {
        this.unresolved(
          lease,
          action.actionId,
          "Execution attempt ceiling reached; operator reconciliation required",
        );
        return "waiting";
      }
      // Both records are durable before the first external byte can leave.
      const started = this.store.fenced(lease, () => {
        if (this.store.run(lease.runId)?.desired !== "run" || signal.aborted)
          return false;
        this.store.db
          .prepare(
            "UPDATE runtime_actions SET state='running',attempts=attempts+1,updated_at=? WHERE id=?",
          )
          .run(Date.now(), action.actionId);
        this.store.db
          .prepare(
            "UPDATE runtime_outbox SET state='inflight',updated_at=? WHERE action_id=?",
          )
          .run(Date.now(), action.actionId);
        return true;
      });
      if (!started) return "waiting";
      const deadline = AbortSignal.timeout(action.limit.maxDurationMs);
      const combined = AbortSignal.any([signal, deadline]);
      // Gateways must honor AbortSignal and enforce provider limits. Abandoning
      // this wait is uncertain, never proof that a provider did no work.
      const result = await Promise.race([
        Promise.resolve()
          .then(() =>
            this.services.tools.execute(action, reservation!, combined),
          )
          .catch(() => ({
            kind: "unknown" as const,
            reason:
              "Tool transport failed after dispatch; reconcile before retry",
          })),
        new Promise<{ kind: "unknown"; reason: string }>((resolve) => {
          if (combined.aborted)
            resolve({
              kind: "unknown",
              reason: "Execution cancelled or timed out",
            });
          else
            combined.addEventListener(
              "abort",
              () =>
                resolve({
                  kind: "unknown",
                  reason: "Execution cancelled or timed out",
                }),
              { once: true },
            );
        }),
      ]);
      if (!this.store.owned(lease))
        throw new Error("Lease lost after execution");
      if (result.kind !== "receipt") {
        this.unresolved(lease, action.actionId, result.reason);
        return "waiting";
      }
      receipt = result.receipt;
    }
    const prepared = prepareReceipt(receipt, action);
    receipt = prepared.receipt;
    this.store.fenced(lease, () => {
      this.store.db
        .prepare(
          "UPDATE runtime_actions SET receipt_json=?,updated_at=? WHERE id=?",
        )
        .run(JSON.stringify(receipt), Date.now(), action.actionId);
      for (const [rejected, kind] of [
        [prepared.outputRejected, "action.output_rejected"],
        [
          prepared.accountingMetadataOmitted,
          "action.accounting_metadata_omitted",
        ],
      ] as const) {
        if (
          rejected &&
          !this.store.db
            .prepare(
              "SELECT 1 FROM runtime_events WHERE run_id=? AND kind=? AND detail=? LIMIT 1",
            )
            .get(action.runId, kind, action.actionId)
        )
          this.store.event(action.runId, kind, action.actionId);
      }
    });
    const settlement = await this.dependency("economy.settle", () =>
      this.services.economy.settle(reservation!, receipt!),
    );
    if (receipt.usage.provisional) {
      // Preserve and post known usage, while keeping the action recoverable.
      // A module's acknowledgement cannot make a provisional cost final.
      this.unresolved(
        lease,
        action.actionId,
        "Provider usage remains provisional; awaiting final reconciliation",
      );
      return pendingState();
    }
    if (settlement.kind !== "settled") {
      this.unresolved(lease, action.actionId, settlement.reason);
      return pendingState();
    }
    const outputRejected = Boolean(
      this.store.db
        .prepare(
          "SELECT 1 FROM runtime_events WHERE run_id=? AND kind='action.output_rejected' AND detail=? LIMIT 1",
        )
        .get(action.runId, action.actionId),
    );
    this.store.fenced(lease, () => {
      this.store.db
        .prepare(
          "UPDATE runtime_actions SET state=?,reason=?,updated_at=? WHERE id=?",
        )
        .run(
          receipt.status === "succeeded" && !outputRejected
            ? "completed"
            : "failed",
          outputRejected
            ? "Tool output was unusable; confirmed outcome and charges preserved"
            : null,
          Date.now(),
          action.actionId,
        );
      this.store.db
        .prepare(
          "UPDATE runtime_outbox SET state='delivered',updated_at=? WHERE action_id=?",
        )
        .run(Date.now(), action.actionId);
      this.store.event(action.runId, "action.settled", action.actionId);
    });
    return BigInt(receipt.usage.actualCharge.micros) >
      BigInt(reservation.limit.maximum.micros)
      ? "exhausted"
      : "done";
  }
  private unresolved(lease: Lease, id: string, reason: string) {
    this.store.fenced(lease, () => {
      this.store.db
        .prepare(
          "UPDATE runtime_actions SET state='unresolved',reason=?,updated_at=? WHERE id=?",
        )
        .run(reason.slice(0, 2000), Date.now(), id);
      this.store.db
        .prepare(
          "UPDATE runtime_outbox SET state='unresolved',updated_at=? WHERE action_id=?",
        )
        .run(Date.now(), id);
    });
  }
}
