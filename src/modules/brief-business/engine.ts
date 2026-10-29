import { createHash } from "node:crypto";
import type { DatabaseSync, SQLInputValue } from "node:sqlite";
import type {
  ActionView,
  Business,
  BusinessSnapshot,
  Candidate,
  FinancialSummary,
  Json,
  WorkSelection,
} from "../../contracts/index.ts";
import { money } from "../../contracts/index.ts";
import { transaction } from "../../db/database.ts";
import { Store } from "../../runtime/store.ts";
import { defaultPolicy, validatePolicy } from "./policy.ts";
import type { BusinessPolicy } from "./policy.ts";
import {
  email,
  https,
  integer,
  record,
  researchBrief,
  text,
} from "./services.ts";
import type { BusinessServiceDefinition } from "./services.ts";

type Row = Record<string, string | number | null>;
export type CommerceSnapshot = import("./commerce.ts").CommerceOrder;
export interface CommerceReader {
  order(request: {
    runId: string;
    jobId: string;
  }): Promise<
    | { kind: "available"; order: unknown }
    | { kind: "unavailable"; reason: string }
  >;
}
export const identity = (...parts: unknown[]) =>
  createHash("sha256").update(JSON.stringify(parts)).digest("hex").slice(0, 40);
const json = (value: unknown) => JSON.stringify(value);
const parse = (value: unknown) =>
  typeof value === "string" ? JSON.parse(value) : value;
const terminal = new Set(["completed", "cancelled", "failed"]);
export class BusinessService implements Business {
  readonly db: DatabaseSync;
  readonly runtime: Store;
  readonly commerce: CommerceReader;
  readonly services: Map<string, BusinessServiceDefinition>;
  readonly contactEmail: string | null;
  readonly validateScope:
    | ((
        service: string,
        scope: Record<string, Json>,
        allowance: string,
      ) => void)
    | undefined;
  constructor(
    db: DatabaseSync,
    commerce: CommerceReader,
    options: {
      services?: BusinessServiceDefinition[];
      contactEmail?: string;
      validateScope?: (
        service: string,
        scope: Record<string, Json>,
        allowance: string,
      ) => void;
    } = {},
  ) {
    this.db = db;
    this.runtime = new Store(db);
    this.commerce = commerce;
    this.services = new Map(
      (options.services ?? [researchBrief]).map((service) => [
        service.name,
        service,
      ]),
    );
    this.contactEmail = options.contactEmail
      ? email(options.contactEmail)
      : null;
    this.validateScope = options.validateScope;
  }
  health() {
    return { available: true as const };
  }
  one(sql: string, ...args: SQLInputValue[]): Row | undefined {
    return this.db.prepare(sql).get(...args) as Row | undefined;
  }
  all(sql: string, ...args: SQLInputValue[]): Row[] {
    return this.db.prepare(sql).all(...args) as Row[];
  }
  run(runId: string) {
    const run = this.runtime.run(runId);
    if (!run || run.profile !== "foundation")
      throw new Error("A foundation runtime run is required");
    return run;
  }
  policy(runId: string): BusinessPolicy {
    const row = this.one(
      "SELECT document FROM business_settings WHERE run_id=?",
      runId,
    );
    return row
      ? validatePolicy(parse(row.document))
      : structuredClone(defaultPolicy);
  }
  memory(
    runId: string,
    entity: string,
    kind: string,
    document: unknown,
    now: string,
  ) {
    this.db
      .prepare("INSERT OR IGNORE INTO business_memory VALUES(?,?,?,?,?,?)")
      .run(
        identity(runId, entity, kind, document),
        runId,
        entity,
        kind,
        json(document),
        now,
      );
  }
  customer(runId: string, address: unknown, now: string) {
    const normalized = email(address),
      id = identity(runId, normalized);
    this.db
      .prepare("INSERT OR IGNORE INTO business_customers VALUES(?,?,?,0,?)")
      .run(id, runId, normalized, now);
    return id;
  }
  enqueue(runId: string, requestId: string, command: Record<string, Json>) {
    this.run(runId);
    text(requestId, 200);
    if (Buffer.byteLength(json(command)) > 12000)
      throw new Error("Business command exceeds the HTTP budget");
    return this.runtime.deliverEvent(runId, {
      source: "operator",
      accountRef: "operator",
      externalEventId: `business:${identity(runId, requestId)}`,
      kind: "business.command",
      payload: command,
    });
  }
  private applyCommand(
    runId: string,
    eventId: string,
    command: Record<string, unknown>,
    now: string,
    financial: FinancialSummary | null,
  ) {
    if (
      this.one(
        "SELECT event_id FROM business_commands WHERE event_id=?",
        eventId,
      )
    )
      return;
    let outcome: Record<string, Json>;
    try {
      outcome = this.command(runId, eventId, command, now, financial);
    } catch {
      outcome = {
        recorded: false,
        reason: "Invalid request or unavailable business transition",
      };
    }
    this.db
      .prepare("INSERT INTO business_commands VALUES(?,?,?,?,?)")
      .run(eventId, runId, json(command), json(outcome), now);
    this.memory(runId, eventId, "command", outcome, now);
  }
  private command(
    runId: string,
    requestId: string,
    command: Record<string, unknown>,
    now: string,
    financial: FinancialSummary | null,
  ): Record<string, Json> {
    const policy = this.policy(runId);
    if (command.command === "intake") {
      const service = text(command.service, 100),
        scope = record(command.scope);
      if (Buffer.byteLength(json(scope)) > 10000)
        throw new Error("Scope exceeds budget");
      const origin = command.origin ?? "inbound";
      if (
        typeof origin !== "string" ||
        !["inbound", "outbound", "discovered"].includes(origin)
      )
        throw new Error("Invalid origin");
      const reference =
        origin === "inbound"
          ? String(command.sourceReference ?? "").slice(0, 2048)
          : https(command.sourceReference);
      const customerId = command.customerEmail
        ? this.customer(runId, command.customerEmail, now)
        : null;
      const id = identity(runId, "opportunity", requestId);
      this.db
        .prepare(
          "INSERT OR IGNORE INTO business_opportunities(id,run_id,customer_id,service,scope,origin,source_reference,priority,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        )
        .run(
          id,
          runId,
          customerId,
          service,
          json(scope),
          String(origin),
          reference,
          integer(command.priority ?? 1, 0, 100),
          "received",
          now,
        );
      return { recorded: true, opportunityId: id };
    }
    if (command.command === "policy") {
      const next = validatePolicy(command.policy);
      this.db
        .prepare(
          "INSERT INTO business_settings VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET document=excluded.document",
        )
        .run(runId, json(next));
      this.memory(runId, runId, "policy", next, now);
      return { saved: true };
    }
    if (command.command === "accept") {
      const quote = this.one(
        "SELECT q.*,o.run_id,o.customer_id,o.service,c.email,c.opted_out FROM business_quotes q JOIN business_opportunities o ON o.id=q.opportunity_id JOIN business_customers c ON c.id=o.customer_id WHERE q.id=? AND o.run_id=?",
        text(command.quoteId, 200),
        runId,
      );
      if (
        !quote ||
        quote.email !== email(command.customerEmail) ||
        quote.opted_out === 1
      )
        throw new Error("Customer does not own the quote");
      const evidence = text(command.evidenceReference, 1000);
      const previous = this.one(
        "SELECT id FROM business_orders WHERE quote_id=?",
        String(quote.id),
      );
      if (previous) return { recorded: true, orderId: String(previous.id) };
      if (quote.status !== "offered" || String(quote.expires_at) <= now)
        throw new Error("Quote unavailable");
      if (
        !financial ||
        BigInt(financial.spendableMicros) < BigInt(String(quote.cost_micros))
      )
        throw new Error("Operating credit cannot fund the quoted work");
      const open = Number(
        this.one(
          "SELECT COUNT(*) n FROM business_orders WHERE run_id=? AND status NOT IN ('completed','cancelled','failed')",
          runId,
        )?.n ?? 0,
      );
      if (open >= policy.maxOpenOrders)
        throw new Error("Commitment capacity reached");
      const id = identity(runId, "order", quote.id);
      this.db
        .prepare(
          "INSERT INTO business_orders(id,run_id,quote_id,customer_id,service,scope,status,price_micros,cost_micros,created_at) VALUES(?,?,?,?,?,?,'accepted',?,?,?)",
        )
        .run(
          id,
          runId,
          String(quote.id),
          String(quote.customer_id),
          String(quote.service),
          String(quote.scope),
          String(quote.price_micros),
          String(quote.cost_micros),
          now,
        );
      this.db
        .prepare("UPDATE business_quotes SET status='accepted' WHERE id=?")
        .run(String(quote.id));
      this.db
        .prepare(
          "UPDATE business_opportunities SET status='ordered' WHERE id=?",
        )
        .run(String(quote.opportunity_id));
      this.memory(
        runId,
        id,
        "acceptance",
        { evidenceReference: evidence },
        now,
      );
      return { recorded: true, orderId: id };
    }
    if (command.command === "opt-out") {
      const customerId = this.customer(runId, command.email, now);
      this.db
        .prepare("UPDATE business_customers SET opted_out=1 WHERE id=?")
        .run(customerId);
      return { recorded: true };
    }
    if (command.command === "resolve-hold") {
      const order = this.one(
        "SELECT * FROM business_orders WHERE id=? AND run_id=?",
        text(command.orderId, 200),
        runId,
      );
      if (!order || order.status !== "paymentHold")
        throw new Error("No payment hold");
      text(command.evidenceReference, 1000);
      if (command.decision === "cancel")
        this.db
          .prepare("UPDATE business_orders SET status='cancelled' WHERE id=?")
          .run(String(order.id));
      else if (
        command.decision === "resume" &&
        this.paid(parse(order.payment), String(order.price_micros))
      )
        this.db
          .prepare("UPDATE business_orders SET status=? WHERE id=?")
          .run(
            order.artifact ? "readyForCommitment" : "creating",
            String(order.id),
          );
      else throw new Error("Verified payment is required before resuming");
      this.memory(
        runId,
        String(order.id),
        "holdResolution",
        {
          decision: String(command.decision),
          evidenceReference: String(command.evidenceReference),
        },
        now,
      );
      return { recorded: true };
    }
    if (command.command === "wake") {
      text(command.evidenceReference, 1000);
      if (command.opportunityId) {
        const opportunity = this.one(
          "SELECT * FROM business_opportunities WHERE id=? AND run_id=?",
          text(command.opportunityId, 200),
          runId,
        );
        if (
          !opportunity ||
          !["needsReview", "budgetWait"].includes(String(opportunity.status))
        )
          throw new Error("Opportunity cannot be reassessed");
        this.db
          .prepare(
            "UPDATE business_opportunities SET status='received' WHERE id=?",
          )
          .run(String(opportunity.id));
      }
      return { scheduled: true };
    }
    if (command.command === "sync-inbox") {
      this.db
        .prepare(
          "INSERT OR IGNORE INTO business_inbox_requests VALUES(?,?,NULL,'pending',?)",
        )
        .run(identity(runId, requestId, "inbox"), runId, now);
      return { scheduled: true };
    }
    throw new Error("Unknown business command");
  }
  private paid(value: unknown, price: string): boolean {
    try {
      const payment = record(value);
      return (
        payment.paymentStatus === "settled" &&
        BigInt(String(payment.receiptsMicros)) -
          BigInt(String(payment.refundsMicros)) >=
          BigInt(price) &&
        BigInt(String(payment.pendingLiabilityMicros ?? "0")) === 0n &&
        BigInt(String(payment.chargebacksMicros ?? "0")) <=
          BigInt(String(payment.chargebackReversalsMicros ?? "0"))
      );
    } catch {
      return false;
    }
  }
  private evaluate(
    snapshot: BusinessSnapshot,
    order: Row,
    payment: Record<string, unknown>,
  ) {
    const actions = snapshot.actions.filter(
        (view) => view.action.jobId === order.id,
      ),
      receipts = actions.filter((view) => view.receipt !== null);
    const charges = receipts.reduce(
      (sum, view) => sum + BigInt(view.receipt!.usage.actualCharge.micros),
      0n,
    );
    const costsFinal =
      payment.costsFinal === true &&
      actions.length > 0 &&
      actions.every(
        (view) =>
          ["completed", "failed"].includes(view.state) &&
          view.receipt !== null &&
          !view.receipt.usage.provisional,
      );
    const earned = BigInt(String(payment.earnedRevenueMicros ?? "0")),
      fees = BigInt(String(payment.processorFeesMicros ?? "0"));
    this.memory(
      snapshot.runId,
      String(order.id),
      "evaluation",
      {
        paymentRevision: String(payment.revision),
        paymentStatus: String(payment.paymentStatus),
        receiptsMicros: String(payment.receiptsMicros ?? "0"),
        refundsMicros: String(payment.refundsMicros ?? "0"),
        earnedRevenueMicros: String(earned),
        processorFeesMicros: String(fees),
        operatingChargesMicros: String(charges),
        costsFinal,
        measuredContributionMicros: costsFinal
          ? String(earned - fees - charges)
          : null,
        lesson:
          "Compare verified receipts, earned revenue and actual tool charges before repeating work. This measured boundary does not establish full business profitability.",
      },
      snapshot.now,
    );
  }
  private mail(
    runId: string,
    entity: Row,
    stage: string,
    subject: string,
    body: unknown,
    attachment?: Json,
  ): Candidate {
    const customer = this.one(
      "SELECT email,opted_out FROM business_customers WHERE id=? AND run_id=?",
      String(entity.customer_id),
      runId,
    );
    if (!customer || customer.opted_out === 1)
      throw new Error("Customer opted out or unavailable");
    return {
      workKey: `business:${entity.id}:${stage}`,
      tool: "gmail.send",
      jobId: String(entity.id),
      input: {
        to: String(customer.email),
        subject,
        body: typeof body === "string" ? body : json(body),
        ...(attachment ? { attachment } : {}),
      },
      limit: {
        maximum: money("0"),
        maxDurationMs: 30000,
        maxOutputBytes: 65536,
      },
    };
  }
  private processReceipt(snapshot: BusinessSnapshot, view: ActionView) {
    const { action, receipt } = view;
    if (
      !action.workKey.startsWith("business:") ||
      !["completed", "failed"].includes(view.state) ||
      !receipt ||
      this.one(
        "SELECT action_id FROM business_receipts WHERE action_id=?",
        action.actionId,
      )
    )
      return;
    const parts = action.workKey.split(":"),
      entityId = parts[1]!,
      stage = parts[2]!;
    const order = this.one(
      "SELECT * FROM business_orders WHERE id=? AND run_id=?",
      entityId,
      snapshot.runId,
    );
    const opportunity = this.one(
      "SELECT * FROM business_opportunities WHERE id=? AND run_id=?",
      entityId,
      snapshot.runId,
    );
    if (receipt.status !== "succeeded") {
      if (order)
        this.db
          .prepare("UPDATE business_orders SET status='needsReview' WHERE id=?")
          .run(entityId);
      if (opportunity)
        this.db
          .prepare(
            "UPDATE business_opportunities SET status='needsReview' WHERE id=?",
          )
          .run(entityId);
      this.memory(
        snapshot.runId,
        entityId,
        "failedAction",
        {
          actionId: action.actionId,
          stage,
          reason: "Confirmed action failure; a new approved intent is required",
        },
        snapshot.now,
      );
    } else {
      const result = record(receipt.result);
      if (
        stage === "offer" &&
        opportunity &&
        ["accepted", "confirmed"].includes(String(result.deliveryState))
      ) {
        this.db
          .prepare(
            "UPDATE business_quotes SET status='offered' WHERE opportunity_id=? AND status='pending'",
          )
          .run(entityId);
        this.db
          .prepare(
            "UPDATE business_opportunities SET status='offered' WHERE id=? AND status='offering'",
          )
          .run(entityId);
      } else if (stage === "checkout" && order) {
        const payment = record(result.order ?? result);
        if (
          payment.runId !== snapshot.runId ||
          payment.jobId !== order.id ||
          payment.currency !== "USD" ||
          !["live", "sandbox"].includes(String(payment.mode))
        )
          throw new Error("Commerce result identity mismatch");
        if (payment.checkoutUrl) https(payment.checkoutUrl);
        this.db
          .prepare(
            "UPDATE business_orders SET payment=?,payment_observed_at=?,status='awaitingPayment' WHERE id=?",
          )
          .run(json(payment), snapshot.now, entityId);
      } else if (stage === "payment-request" && order) {
        this.memory(
          snapshot.runId,
          entityId,
          "paymentRequest",
          { actionId: action.actionId },
          snapshot.now,
        );
      } else if (stage === "commitment" && order) {
        this.db
          .prepare(
            "UPDATE business_orders SET status='ready' WHERE id=? AND status='readyForCommitment'",
          )
          .run(entityId);
      } else if (stage === "create" && order) {
        const service = this.services.get(String(order.service));
        if (!service) throw new Error("Service validator unavailable");
        const artifact = service.artifact(parse(order.scope), result);
        const quote = this.one(
          "SELECT turnaround_seconds FROM business_quotes WHERE id=?",
          String(order.quote_id),
        )!;
        const startedAt =
          typeof result.executionStartedAt === "string"
            ? Date.parse(result.executionStartedAt)
            : NaN;
        if (
          !Number.isFinite(startedAt) ||
          !/(Z|\+00:00)$/.test(String(result.executionStartedAt)) ||
          startedAt > Date.parse(snapshot.now) + 1000
        ) {
          this.db
            .prepare(
              "UPDATE business_orders SET artifact=?,status='needsReview' WHERE id=?",
            )
            .run(json(artifact), entityId);
          this.memory(
            snapshot.runId,
            entityId,
            "deadlineUnknown",
            {
              actionId: action.actionId,
              reason:
                "Original paid dispatch time unavailable; retain the checked artifact for review",
            },
            snapshot.now,
          );
          this.db
            .prepare("INSERT OR IGNORE INTO business_receipts VALUES(?,?,?,?)")
            .run(action.actionId, snapshot.runId, action.workKey, snapshot.now);
          return;
        }
        const due =
          order.delivery_due_at ??
          new Date(
            startedAt + Number(quote.turnaround_seconds) * 1000,
          ).toISOString();
        this.db
          .prepare(
            "UPDATE business_orders SET artifact=?,status='readyForCommitment',delivery_due_at=? WHERE id=? AND status='creating'",
          )
          .run(json(artifact), String(due), entityId);
      } else if (stage === "delivery" && order) {
        if (!["accepted", "confirmed"].includes(String(result.deliveryState)))
          throw new Error("Delivery transport evidence missing");
        // Transport acceptance alone never recognizes delivery or revenue.
        this.db
          .prepare(
            "UPDATE business_orders SET status='awaitingAcknowledgment' WHERE id=? AND status='ready'",
          )
          .run(entityId);
      } else if (stage === "fulfill" && order) {
        const payment = record(result.order ?? result);
        if (
          payment.runId !== snapshot.runId ||
          payment.jobId !== order.id ||
          payment.deliveryObligation !== "fulfilled"
        )
          throw new Error("Fulfillment not verified");
        this.db
          .prepare(
            "UPDATE business_orders SET status='completed',payment=?,payment_observed_at=?,completed_at=? WHERE id=? AND status='delivered'",
          )
          .run(json(payment), snapshot.now, snapshot.now, entityId);
        this.db
          .prepare(
            "INSERT OR IGNORE INTO business_public_activity VALUES(?,?,?,?)",
          )
          .run(
            identity(snapshot.runId, entityId, "completed"),
            snapshot.runId,
            String(order.service),
            snapshot.now,
          );
        this.evaluate(snapshot, order, payment);
      } else if (stage === "inbox") {
        this.inbox(snapshot, result);
        this.db
          .prepare(
            "UPDATE business_inbox_requests SET state='done' WHERE id=? AND run_id=?",
          )
          .run(entityId, snapshot.runId);
        if (result.nextCursor) {
          const cursor = text(result.nextCursor, 2048);
          this.db
            .prepare(
              "INSERT OR IGNORE INTO business_inbox_requests VALUES(?,?,?,'pending',?)",
            )
            .run(
              identity(entityId, cursor),
              snapshot.runId,
              cursor,
              snapshot.now,
            );
        }
      }
    }
    this.db
      .prepare("INSERT OR IGNORE INTO business_receipts VALUES(?,?,?,?)")
      .run(action.actionId, snapshot.runId, action.workKey, snapshot.now);
  }
  private inbox(snapshot: BusinessSnapshot, result: Record<string, unknown>) {
    if (!Array.isArray(result.messages) || result.messages.length > 100)
      throw new Error("Invalid inbox batch");
    for (const raw of result.messages) {
      try {
        const message = record(raw);
        if (message.senderVerified !== true) continue;
        const messageId = text(message.messageId, 300),
          sender = email(message.from);
        if (
          this.one(
            "SELECT id FROM business_mail WHERE run_id=? AND message_id=?",
            snapshot.runId,
            messageId,
          )
        )
          continue;
        const command = record(message.command),
          normalized: Record<string, unknown> = { ...command };
        if (command.type === "request")
          Object.assign(normalized, {
            command: "intake",
            customerEmail: sender,
            scope: command.scope,
            sourceReference: messageId,
          });
        else if (command.type === "accept")
          Object.assign(normalized, {
            command: "accept",
            quoteId: command.quoteId ?? command.quote_id,
            customerEmail: sender,
            evidenceReference: messageId,
          });
        else if (command.type === "opt_out")
          Object.assign(normalized, { command: "opt-out", email: sender });
        else continue;
        this.applyCommand(
          snapshot.runId,
          identity("gmail", snapshot.runId, messageId),
          normalized,
          snapshot.now,
          snapshot.financial,
        );
        this.db
          .prepare("INSERT OR IGNORE INTO business_mail VALUES(?,?,?,?,?)")
          .run(
            identity(snapshot.runId, messageId),
            snapshot.runId,
            messageId,
            sender,
            snapshot.now,
          );
      } catch {
        this.memory(
          snapshot.runId,
          "inbox",
          "needsClarification",
          { reason: "Invalid or unauthenticated commercial message" },
          snapshot.now,
        );
      }
    }
    if (Array.isArray(result.deliveryUpdates))
      for (const raw of result.deliveryUpdates) {
        const update = record(raw);
        if (update.deliveryState !== "confirmed") continue;
        const sent = snapshot.actions.find(
          (item) =>
            item.action.actionId === update.actionId &&
            item.action.jobId === update.jobId &&
            item.action.tool === "gmail.send" &&
            record(item.action.input).attachment,
        );
        const order = this.one(
          "SELECT * FROM business_orders WHERE id=? AND run_id=?",
          String(update.jobId),
          snapshot.runId,
        );
        if (
          !sent ||
          !order ||
          !["ready", "awaitingAcknowledgment", "delivered"].includes(
            String(order.status),
          )
        )
          continue;
        const reference = text(update.evidenceRef, 1000);
        this.db
          .prepare(
            "INSERT OR IGNORE INTO business_delivery_evidence VALUES(?,?,?,?,?)",
          )
          .run(
            String(update.actionId),
            snapshot.runId,
            String(order.id),
            reference,
            snapshot.now,
          );
        this.db
          .prepare(
            "UPDATE business_orders SET status='delivered',delivery_reference=? WHERE id=?",
          )
          .run(reference, String(order.id));
        this.memory(
          snapshot.runId,
          String(order.id),
          "verifiedDelivery",
          { reference, actionId: String(update.actionId) },
          snapshot.now,
        );
      }
  }
  async selectWork(snapshot: BusinessSnapshot): Promise<WorkSelection> {
    const run = this.run(snapshot.runId),
      revision = run.revision;
    if (run.desired !== "run")
      return { kind: "wait", reason: "Operator control blocks new work" };
    const acknowledgeEventIds = snapshot.events
      .filter(
        (event) =>
          event.kind === "business.command" && event.source === "operator",
      )
      .map((event) => event.eventId);
    const active = () =>
      this.runtime.run(snapshot.runId)?.revision === revision &&
      this.runtime.run(snapshot.runId)?.desired === "run";
    transaction(this.db, () => {
      if (!active()) return;
      this.db
        .prepare("INSERT OR IGNORE INTO business_settings VALUES(?,?)")
        .run(snapshot.runId, json(defaultPolicy));
      for (const view of snapshot.actions) {
        try {
          this.processReceipt(snapshot, view);
        } catch {
          if (view.action.jobId)
            this.db
              .prepare(
                "UPDATE business_orders SET status='needsReview' WHERE id=? AND run_id=?",
              )
              .run(view.action.jobId, snapshot.runId);
          this.memory(
            snapshot.runId,
            view.action.jobId ?? view.action.actionId,
            "invalidReceipt",
            {
              actionId: view.action.actionId,
              reason:
                "Persisted result needs operator review; no automatic paid retry",
            },
            snapshot.now,
          );
          this.db
            .prepare("INSERT OR IGNORE INTO business_receipts VALUES(?,?,?,?)")
            .run(
              view.action.actionId,
              snapshot.runId,
              view.action.workKey,
              snapshot.now,
            );
        }
      }
      for (const event of snapshot.events)
        if (acknowledgeEventIds.includes(event.eventId))
          this.applyCommand(
            snapshot.runId,
            event.eventId,
            record(event.payload),
            snapshot.now,
            snapshot.financial,
          );
    });
    if (!active())
      return {
        kind: "wait",
        reason: "Operator changed intent during selection",
      };
    const policy = this.policy(snapshot.runId),
      available = (tool: string) =>
        snapshot.capabilities.some(
          (item) => item.id === tool && item.availability.available,
        );
    const work = (candidate: Candidate): WorkSelection =>
      available(candidate.tool)
        ? { kind: "work", candidate, acknowledgeEventIds }
        : {
            kind: "wait",
            reason: `Required capability unavailable: ${candidate.tool}`,
            acknowledgeEventIds,
          };
    const sync = this.one(
      "SELECT * FROM business_inbox_requests WHERE run_id=? AND state='pending' ORDER BY created_at,id LIMIT 1",
      snapshot.runId,
    );
    if (sync)
      return work({
        workKey: `business:${sync.id}:inbox`,
        tool: "gmail.read",
        input: sync.cursor ? { cursor: String(sync.cursor) } : {},
        limit: {
          maximum: money("0"),
          maxDurationMs: 30000,
          maxOutputBytes: 1000000,
        },
      });
    const orders = this.all(
      "SELECT * FROM business_orders WHERE run_id=? AND status NOT IN ('completed','cancelled','failed','needsReview') ORDER BY created_at,id",
      snapshot.runId,
    );
    // Verify one order per selection, rotating after each bounded observation.
    const observedOrders = this.all(
      "SELECT * FROM business_orders WHERE run_id=? ORDER BY id",
      snapshot.runId,
    );
    const cursor = this.one(
      "SELECT * FROM business_cursors WHERE run_id=?",
      snapshot.runId,
    );
    const turn = Number(cursor?.turn ?? 0);
    const actionable = orders.find((order) =>
      ["creating", "readyForCommitment", "ready"].includes(
        String(order.status),
      ),
    );
    const waiting = observedOrders.filter((order) =>
      ["awaitingPayment", "paymentHold"].includes(String(order.status)),
    );
    const history = observedOrders.filter(
      (order) =>
        ![
          "accepted",
          "creating",
          "readyForCommitment",
          "ready",
          "awaitingPayment",
          "paymentHold",
        ].includes(String(order.status)),
    );
    const historyTurn = history.length > 0 && turn % 4 === 3;
    const pool = historyTurn
      ? history
      : waiting.length
        ? waiting
        : history.length
          ? history
          : observedOrders;
    const previous = String(
      historyTurn || (!actionable && !waiting.length)
        ? (cursor?.history_id ?? "")
        : (cursor?.order_id ?? ""),
    );
    const observed =
      (!historyTurn ? actionable : undefined) ??
      pool.find((order) => String(order.id) > previous) ??
      pool[0];
    let paymentVerified = false;
    if (observed && observed.status !== "accepted") {
      const current = await this.commerce.order({
        runId: snapshot.runId,
        jobId: String(observed.id),
      });
      if (!active())
        return {
          kind: "wait",
          reason: "Operator changed intent during payment observation",
        };
      if (current.kind === "available") {
        const payment = record(current.order);
        if (
          payment.runId !== snapshot.runId ||
          payment.jobId !== observed.id ||
          payment.currency !== "USD" ||
          payment.mode !== snapshot.financial?.mode
        )
          return {
            kind: "wait",
            reason: "Payment identity or mode unavailable",
            acknowledgeEventIds,
          };
        this.db
          .prepare(
            "UPDATE business_orders SET payment=?,payment_observed_at=? WHERE id=?",
          )
          .run(json(payment), snapshot.now, String(observed.id));
        paymentVerified = true;
        observed.payment = json(payment);
        if (
          !this.paid(payment, String(observed.price_micros)) &&
          ["creating", "readyForCommitment", "ready"].includes(
            String(observed.status),
          )
        ) {
          this.db
            .prepare(
              "UPDATE business_orders SET status='paymentHold' WHERE id=?",
            )
            .run(String(observed.id));
          observed.status = "paymentHold";
        }
        this.memory(
          snapshot.runId,
          String(observed.id),
          "financialObservation",
          {
            revision: String(payment.revision),
            paymentStatus: String(payment.paymentStatus),
            receiptsMicros: String(payment.receiptsMicros),
            refundsMicros: String(payment.refundsMicros),
            earnedRevenueMicros: String(payment.earnedRevenueMicros),
          },
          snapshot.now,
        );
        if (
          terminal.has(String(observed.status)) ||
          observed.status === "needsReview"
        )
          this.evaluate(snapshot, observed, payment);
      }
      this.db
        .prepare(
          "INSERT INTO business_cursors VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET order_id=excluded.order_id,history_id=excluded.history_id,turn=excluded.turn",
        )
        .run(
          snapshot.runId,
          history.includes(observed)
            ? String(cursor?.order_id ?? "")
            : String(observed.id),
          history.includes(observed)
            ? String(observed.id)
            : String(cursor?.history_id ?? ""),
          (turn + 1) % 4,
        );
    }
    for (const order of orders) {
      const id = String(order.id),
        stage = (name: string) =>
          snapshot.actions.find(
            (item) => item.action.workKey === `business:${id}:${name}`,
          );
      const payment =
        observed && order.id === observed.id
          ? parse(observed.payment)
          : parse(order.payment);
      if (observed && observed.id === order.id)
        order.status = observed.status ?? null;
      if (order.status === "accepted")
        return work({
          workKey: `business:${id}:checkout`,
          tool: "commerce.checkout",
          jobId: id,
          input: {
            amountMicros: String(order.price_micros),
            currency: "USD",
            description: "WideMind agreed scoped order",
          },
          limit: {
            maximum: money("0"),
            maxDurationMs: 30000,
            maxOutputBytes: 65536,
          },
        });
      if (order.status === "awaitingPayment") {
        if (payment?.checkoutUrl && !stage("payment-request"))
          return work(
            this.mail(
              snapshot.runId,
              order,
              "payment-request",
              "WideMind payment request",
              {
                orderId: id,
                paymentUrl: payment.checkoutUrl,
                terms:
                  "Delivery starts after verified payment and operating authorization",
              },
            ),
          );
        if (
          paymentVerified &&
          this.paid(payment, String(order.price_micros)) &&
          observed?.id === order.id
        ) {
          this.db
            .prepare("UPDATE business_orders SET status='creating' WHERE id=?")
            .run(id);
          order.status = "creating";
        }
      }
      if (
        ["creating", "readyForCommitment", "ready"].includes(
          String(order.status),
        )
      ) {
        if (observed?.id !== order.id || !paymentVerified)
          return {
            kind: "wait",
            reason: "Verify current payment before the next fulfillment stage",
            wakeAt: new Date(Date.parse(snapshot.now) + 1000).toISOString(),
            acknowledgeEventIds,
          };
        if (!this.paid(payment, String(order.price_micros))) continue;
        const service = this.services.get(String(order.service));
        if (
          !service ||
          !policy.capabilities.includes(service.capability) ||
          BigInt(String(order.cost_micros)) > BigInt(policy.maxJobMicros)
        )
          continue;
        if (order.status === "readyForCommitment")
          return work(
            this.mail(
              snapshot.runId,
              order,
              "commitment",
              "WideMind delivery commitment",
              {
                orderId: id,
                scope: parse(order.scope),
                deliveryDueAt: order.delivery_due_at,
              },
            ),
          );
        if (order.status === "creating")
          return work({
            workKey: `business:${id}:create`,
            tool: service.tool,
            jobId: id,
            input: service.normalize(parse(order.scope)),
            limit: {
              maximum: money(String(order.cost_micros)),
              maxTokens: 30000,
              maxDurationMs: 120000,
              maxOutputBytes: 1000000,
            },
          });
        if (order.status === "ready")
          return work(
            this.mail(
              snapshot.runId,
              order,
              "delivery",
              "WideMind private delivery",
              { orderId: id, deliveryDueAt: order.delivery_due_at },
              parse(order.artifact),
            ),
          );
      }
      if (order.status === "delivered")
        return work({
          workKey: `business:${id}:fulfill`,
          tool: "commerce.fulfill",
          jobId: id,
          input: { deliveryReference: String(order.delivery_reference) },
          limit: {
            maximum: money("0"),
            maxDurationMs: 30000,
            maxOutputBytes: 65536,
          },
        });
      if (
        order.delivery_due_at &&
        String(order.delivery_due_at) < snapshot.now &&
        !terminal.has(String(order.status))
      )
        this.memory(
          snapshot.runId,
          id,
          "deadline",
          {
            deliveryDueAt: String(order.delivery_due_at),
            state:
              order.status === "awaitingAcknowledgment"
                ? "deliveryConfirmationOverdue"
                : "deliveryOverdue",
          },
          snapshot.now,
        );
    }
    const received = this.all(
      "SELECT * FROM business_opportunities WHERE run_id=? AND status='received' ORDER BY priority DESC,created_at,id LIMIT 100",
      snapshot.runId,
    );
    const ranked: {
      opportunity: Row;
      score: bigint;
      scope: Record<string, Json>;
      price: string;
      assessment: Record<string, Json>;
    }[] = [];
    for (const opportunity of received) {
      let assessment: Record<string, Json>;
      try {
        const service = this.services.get(String(opportunity.service));
        if (
          !service ||
          !policy.capabilities.includes(service.capability) ||
          !opportunity.customer_id
        )
          throw new Error("Unavailable service or customer");
        const customer = this.one(
          "SELECT opted_out FROM business_customers WHERE id=?",
          String(opportunity.customer_id),
        );
        if (customer?.opted_out === 1) throw new Error("Customer opted out");
        if (opportunity.origin !== "inbound" && !policy.allowOutbound)
          throw new Error("Outbound disabled");
        if (!available(service.tool) || !available("gmail.send"))
          throw new Error("Required tool unavailable");
        const scope = service.normalize(parse(opportunity.scope)),
          price = policy.prices[service.name];
        if (
          !price ||
          BigInt(price) <= BigInt(policy.maxJobMicros) ||
          BigInt(policy.maxJobMicros) < 1n ||
          BigInt(price) < 500000n ||
          BigInt(price) > 999999990000n ||
          BigInt(price) % 10000n !== 0n
        )
          throw new Error(
            "Price must exceed cost and meet exact processor bounds",
          );
        this.validateScope?.(service.name, scope, policy.maxJobMicros);
        if (
          !snapshot.financial ||
          BigInt(snapshot.financial.spendableMicros) <
            BigInt(policy.maxJobMicros)
        )
          throw new Error("Current operating credit cannot finance this quote");
        assessment = {
          feasible: true,
          expectedCostMicros: policy.maxJobMicros,
          priceMicros: price,
          expectedContributionMicros: String(
            BigInt(price) - BigInt(policy.maxJobMicros),
          ),
        };
        ranked.push({
          opportunity,
          scope,
          price,
          assessment,
          score:
            BigInt(policy.priorityWeight) *
              BigInt(Number(opportunity.priority)) *
              1000000n +
            BigInt(policy.marginWeight) *
              (BigInt(price) - BigInt(policy.maxJobMicros)) +
            BigInt(policy.goalWeights[service.name] ?? 0) * 1000000n,
        });
      } catch {
        assessment = {
          feasible: false,
          reason:
            "Scope, customer, capability, outbound permission or configured price requires review",
        };
        this.db
          .prepare(
            "UPDATE business_opportunities SET status='needsReview',assessment=? WHERE id=?",
          )
          .run(json(assessment), String(opportunity.id));
      }
      this.memory(
        snapshot.runId,
        String(opportunity.id),
        "assessment",
        assessment!,
        snapshot.now,
      );
    }
    ranked.sort((a, b) => (a.score > b.score ? -1 : a.score < b.score ? 1 : 0));
    let used = this.all(
      "SELECT cost_micros FROM business_quotes q JOIN business_opportunities o ON o.id=q.opportunity_id WHERE o.run_id=?",
      snapshot.runId,
    ).reduce((sum, row) => sum + BigInt(String(row.cost_micros)), 0n);
    for (const item of ranked) {
      if (
        used + BigInt(policy.maxJobMicros) >
        BigInt(policy.experimentBudgetMicros)
      ) {
        this.db
          .prepare(
            "UPDATE business_opportunities SET status='budgetWait' WHERE id=?",
          )
          .run(String(item.opportunity.id));
        continue;
      }
      const quoteId = identity(snapshot.runId, "quote", item.opportunity.id);
      this.db
        .prepare(
          "INSERT OR IGNORE INTO business_quotes VALUES(?,?,?,?,?,?,?,'pending')",
        )
        .run(
          quoteId,
          String(item.opportunity.id),
          json(item.scope),
          item.price,
          policy.maxJobMicros,
          policy.turnaroundSeconds,
          new Date(
            Date.parse(snapshot.now) + policy.quoteValidSeconds * 1000,
          ).toISOString(),
        );
      used += BigInt(policy.maxJobMicros);
      this.db
        .prepare(
          "UPDATE business_opportunities SET status='offering',scope=?,assessment=? WHERE id=?",
        )
        .run(
          json(item.scope),
          json(item.assessment),
          String(item.opportunity.id),
        );
    }
    const pendingOffers = this.all(
      "SELECT o.*,q.price_micros,q.cost_micros FROM business_opportunities o JOIN business_quotes q ON q.opportunity_id=o.id WHERE o.run_id=? AND o.status='offering' ORDER BY o.created_at,o.id",
      snapshot.runId,
    );
    const priorityScore = (opportunity: Row) =>
      BigInt(policy.priorityWeight) *
        BigInt(Number(opportunity.priority)) *
        1000000n +
      BigInt(policy.marginWeight) *
        (BigInt(String(opportunity.price_micros)) -
          BigInt(String(opportunity.cost_micros))) +
      BigInt(policy.goalWeights[String(opportunity.service)] ?? 0) * 1000000n;
    pendingOffers.sort((a, b) =>
      priorityScore(a) > priorityScore(b)
        ? -1
        : priorityScore(a) < priorityScore(b)
          ? 1
          : 0,
    );
    const offering = pendingOffers[0];
    if (offering) {
      const quote = this.one(
        "SELECT * FROM business_quotes WHERE opportunity_id=?",
        String(offering.id),
      )!;
      if (String(quote.expires_at) <= snapshot.now) {
        this.db
          .prepare(
            "UPDATE business_opportunities SET status='expired' WHERE id=?",
          )
          .run(String(offering.id));
        return {
          kind: "wait",
          reason: "Quote expired",
          wakeAt: new Date(Date.parse(snapshot.now) + 1000).toISOString(),
          acknowledgeEventIds,
        };
      }
      return work(
        this.mail(snapshot.runId, offering, "offer", "WideMind scoped offer", {
          quoteId: quote.id,
          service: offering.service,
          scope: parse(quote.scope),
          priceMicros: quote.price_micros,
          currency: "USD",
          turnaroundSeconds: quote.turnaround_seconds,
          expiresAt: quote.expires_at,
          terms:
            "Reply with structured JSON {type:accept,quoteId:...} to accept this exact quote. No automatic charge.",
        }),
      );
    }
    return {
      kind: "wait",
      reason: observedOrders.length
        ? "Waiting for verified payment, delivery acknowledgment, capability or operator review"
        : "Waiting for authenticated requests",
      ...(observedOrders.length
        ? { wakeAt: new Date(Date.parse(snapshot.now) + 30000).toISOString() }
        : {}),
      acknowledgeEventIds,
    };
  }
}
