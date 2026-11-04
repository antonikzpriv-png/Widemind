import type { PublishedPilot } from "../runtime/pilot-snapshots.ts";
import { displayUsd, escape, layout } from "./pages.ts";

const CRITERIA: Record<string, string> = {
  accounting_available: "Accounting records available",
  live_mode: "Live money, not a sandbox",
  opening_exactly_5usd: "Started with exactly $5.00",
  no_test_funding: "No test funding",
  customer_receipts: "A customer paid",
  independent_customer: "The customer is independent",
  delivery_costs_covered: "Receipts covered delivery costs",
  no_owner_funding: "No owner top-ups",
  operating_costs_covered: "Covered hosting and other running costs",
  paid_work_delivered: "Paid work was delivered and kept",
  no_overdue_deliveries: "No overdue deliveries",
};
const STATUS: Record<string, string> = {
  met: "Met",
  not_met: "Not met",
  unknown: "Unknown",
  attestation_required: "Awaiting attestation",
};
const VERDICT: Record<string, string> = {
  met: "Milestone met",
  pending_attestation: "Awaiting independence attestation",
  not_met: "Milestone not yet met",
};

function usd(value: string | null | undefined) {
  if (typeof value !== "string") return "Unknown";
  try {
    return displayUsd(value);
  } catch {
    return "Unknown";
  }
}
const stat = (label: string, value: string, note = "") =>
  `<section><div class="stat-label">${escape(label)}</div><div class="stat-value">${escape(value)}</div>${note ? `<p class="muted">${escape(note)}</p>` : ""}</section>`;
const facts = (rows: [string, string][]) =>
  `<dl class="facts">${rows.map(([label, value]) => `<dt>${escape(label)}</dt><dd>${escape(value)}</dd>`).join("")}</dl>`;

export function pilotPage(data: PublishedPilot | null) {
  const intro = `<p class="eyebrow">THE $5 SURVIVAL PILOT</p><h1>Can a $5 start pay for itself?</h1><p class="lead">WideMind started with $5 of operating credit. This page shows published snapshots of its accounting: what customers paid, what the work cost, and every time the operator stepped in.</p>`;
  if (!data?.latest)
    return layout(
      "Pilot",
      `${intro}<section class="notice"><h2>No results published yet</h2><p>Results appear here once the operator publishes a snapshot of the run. Nothing is estimated or assumed in the meantime.</p></section>`,
    );
  const report = data.latest;
  const outcomes = report.outcomes;
  const support = report.outsideSupport;
  const criteria = report.criteria
    .map(
      (item) =>
        `<div class="row"><div class="row-main"><strong>${escape(CRITERIA[item.id] ?? item.id)}</strong><p class="muted">${escape(item.detail)}</p></div><div class="row-meta"><span class="pill">${escape(STATUS[item.status] ?? item.status)}</span>${item.required ? "" : "<p>Not required for the milestone</p>"}</div></div>`,
    )
    .join("");
  const timeline = data.timeline
    .slice()
    .reverse()
    .map(
      (point) =>
        `<tr><td>${escape(point.takenAt.slice(0, 16).replace("T", " "))}</td><td>${escape(VERDICT[point.verdict] ?? point.verdict)}</td><td>${escape(usd(point.spendableMicros))}</td><td>${escape(usd(point.grossCustomerReceiptsMicros))}</td><td>${escape(usd(point.netOperatingResultMicros))}</td><td>${escape(usd(point.resultAfterOutsideCostsMicros))}</td><td>${point.interventions}</td></tr>`,
    )
    .join("");
  const categories = Object.entries(report.outsideLedgerCosts.byCategory);
  return layout(
    "Pilot",
    `${intro}<section class="notice"><h2>${escape(VERDICT[report.verdict] ?? report.verdict)}</h2><p>Snapshot of ${escape(report.snapshot.takenAt)} · accounting ${escape(report.accounting ? `${report.accounting.mode} mode, as of ${report.accounting.asOf}` : "unavailable")} · run ${escape(report.run.state)} since ${escape(report.run.createdAt.slice(0, 10))}.</p></section>
<div class="stats">${stat("Spendable now", usd(outcomes?.spendableMicros), outcomes?.exhausted ? "Credit exhausted: no new paid work" : "")}${stat("Customer receipts", usd(outcomes?.grossCustomerReceiptsMicros), "Verified payments, before fees")}${stat("Net operating result", usd(outcomes?.netOperatingResultMicros), "Net receipts minus delivery costs")}${stat("After running costs", usd(report.resultAfterOutsideCostsMicros), "Also minus hosting and subscriptions")}</div>
<section><h2>Milestone criteria</h2><div class="row-list">${criteria}</div></section>
<div class="grid"><section><h2>Money in and out</h2>${facts([
      ["Gross customer receipts", usd(outcomes?.grossCustomerReceiptsMicros)],
      ["Processor fees", usd(outcomes?.processorFeesMicros)],
      ["Refunds", usd(outcomes?.refundsMicros)],
      ["Chargebacks", usd(outcomes?.chargebacksMicros)],
      ["Net customer receipts", usd(outcomes?.netCustomerReceiptsMicros)],
      ["Delivery and tool costs", usd(outcomes?.operatingChargesMicros)],
      ["Reserved for running work", usd(outcomes?.reservedMicros)],
    ])}</section><section><h2>Help from outside</h2>${facts([
      ["Opening credit", usd(support?.openingCreditMicros)],
      ["Owner top-ups", usd(support?.ownerFundingMicros)],
      ["Test funding", usd(support?.testFundingMicros)],
      ["Infrastructure subsidies (not cash)", usd(support?.subsidiesMicros)],
      ["Operator interventions", String(report.interventions.total)],
      [
        "Customer independence",
        report.attestation.status === "affirmed"
          ? `Attested ${report.attestation.recordedAt?.slice(0, 10) ?? ""}`
          : report.attestation.status === "withdrawn"
            ? "Attestation withdrawn"
            : "Not attested",
      ],
    ])}</section><section><h2>Deliveries</h2>${
      report.deliveries
        ? facts([
            ["Orders", String(report.deliveries.orders)],
            [
              "Paid, delivered and kept",
              String(report.deliveries.paidDeliveredNotRefunded),
            ],
            [
              "Delivered but fully refunded",
              String(report.deliveries.deliveredFullyRefunded),
            ],
            ["Overdue", String(report.deliveries.overdue)],
          ])
        : '<p class="muted">Delivery records unavailable.</p>'
    }</section><section><h2>Running costs outside the ledger</h2>${
      categories.length
        ? facts(
            categories.map(([category, micros]) => [
              category[0]!.toUpperCase() + category.slice(1),
              usd(micros),
            ]),
          )
        : '<p class="muted">None recorded yet.</p>'
    }<p class="muted">Total ${escape(usd(report.outsideLedgerCosts.totalMicros))} across ${report.outsideLedgerCosts.entries} entr${report.outsideLedgerCosts.entries === 1 ? "y" : "ies"}.</p></section></div>
<section><h2>Timeline</h2><div class="table-wrap"><table><thead><tr><th>Snapshot (UTC)</th><th>Verdict</th><th>Spendable</th><th>Receipts</th><th>Net result</th><th>After running costs</th><th>Interventions</th></tr></thead><tbody>${timeline}</tbody></table></div><p class="muted">${data.chain.publishedSnapshots} published snapshot(s). Record chain ${data.chain.ok ? "verifies" : "<strong>does not verify</strong>"}. Latest snapshot hash <code>${escape(report.snapshot.hash)}</code>.</p></section>
<section><h2>What this does not prove</h2>${report.limitations.map((line) => `<p class="muted">${escape(line)}</p>`).join("")}<p><a href="/api/pilot">Machine-readable data →</a></p></section>`,
  );
}
