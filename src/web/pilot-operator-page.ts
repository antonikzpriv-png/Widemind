import type { PilotReadiness, PilotReport } from "../runtime/pilot.ts";
import type { CostEntry } from "../runtime/pilot-records.ts";
import { COST_CATEGORIES } from "../runtime/pilot-records.ts";
import type {
  ChainVerification,
  SnapshotInfo,
} from "../runtime/pilot-snapshots.ts";
import { escape, layout } from "./pages.ts";
import { CRITERIA, STATUS, VERDICT, facts, usd } from "./pilot-page.ts";

export interface OperatorPilotData {
  runId: string;
  readiness: PilotReadiness;
  report: PilotReport;
  costs: CostEntry[];
  snapshots: SnapshotInfo[];
  chain: ChainVerification;
}

export function pilotOperatorPage(
  data: OperatorPilotData | null,
  csrf: string,
) {
  const token = `<input type="hidden" name="csrf" value="${escape(csrf)}">`;
  const form = (
    action: string,
    fields: string,
    button: string,
    secondary = false,
  ) =>
    `<form method="post" action="/operator/pilot/${action}">${token}${fields}<button${secondary ? ' class="secondary"' : ""}>${escape(button)}</button></form>`;
  const field = (label: string, name: string, extra = "") =>
    `<label>${escape(label)}<input name="${name}" ${extra}></label>`;
  const head = `<p class="eyebrow">AUTHENTICATED OPERATOR · $5 SURVIVAL PILOT</p><h1>Pilot</h1><p><a href="/operator">← Run control</a> · <a href="/pilot">Public pilot page →</a></p>`;
  if (!data)
    return layout(
      "Pilot",
      `${head}<section class="notice"><h2>No pilot run</h2><p>Set WIDEMIND_COMMERCE_RUN_ID to an existing run and restart the website.</p></section>`,
    );
  const { report, readiness } = data;
  const checklist = readiness.checks
    .map(
      (item) =>
        `<div class="row"><div class="row-main"><strong>${escape(item.id)}</strong><p class="muted">${escape(item.detail)}</p></div><div class="row-meta"><span class="pill">${item.ok ? "Ready" : "Blocked"}</span></div></div>`,
    )
    .join("");
  const criteria = report.criteria
    .map(
      (item) =>
        `<div class="row"><div class="row-main"><strong>${escape(CRITERIA[item.id] ?? item.id)}</strong><p class="muted">${escape(item.detail)}</p></div><div class="row-meta"><span class="pill">${escape(STATUS[item.status] ?? item.status)}</span>${item.required ? "" : "<p>Not required</p>"}</div></div>`,
    )
    .join("");
  const costs = data.costs.length
    ? `<div class="table-wrap"><table><thead><tr><th>Id</th><th>Category</th><th>Amount</th><th>Period</th><th>Evidence</th><th>Note</th></tr></thead><tbody>${data.costs
        .map(
          (cost) =>
            `<tr><td>${cost.id}${cost.correctsId ? ` <small>corrects ${cost.correctsId}</small>` : ""}</td><td>${escape(cost.category)}</td><td>${escape(usd(cost.amountMicros))}</td><td>${escape(cost.periodStart)} – ${escape(cost.periodEnd)}</td><td>${escape(cost.evidenceRef)}</td><td>${escape(cost.note)}</td></tr>`,
        )
        .join("")}</tbody></table></div>`
    : '<p class="muted">No costs recorded yet.</p>';
  const snapshots = data.snapshots.length
    ? `<div class="table-wrap"><table><thead><tr><th>Id</th><th>Taken (UTC)</th><th>State</th><th>Hash</th><th></th></tr></thead><tbody>${data.snapshots
        .map(
          (item) =>
            `<tr><td>${item.id}</td><td>${escape(item.takenAt.slice(0, 16).replace("T", " "))}</td><td>${item.retractedAt ? "Retracted" : item.published ? "Published" : "Private"}</td><td><code>${escape(item.hash.slice(0, 16))}…</code></td><td>${item.published && !item.retractedAt ? form("retract", `<input type="hidden" name="snapshotId" value="${item.id}">`, "Retract", true) : ""}</td></tr>`,
        )
        .join("")}</tbody></table></div>`
    : '<p class="muted">No snapshots yet.</p>';
  const attestation = report.attestation;
  return layout(
    "Pilot",
    `${head}<section class="notice"><h2>${escape(VERDICT[report.verdict] ?? report.verdict)}</h2><p>Run <code>${escape(data.runId)}</code> · go-live checklist ${readiness.ready ? "passes" : `has ${readiness.checks.filter((item) => !item.ok).length} blocker(s)`} · accounting ${escape(report.accounting?.mode ?? "unavailable")}.</p></section>
<div class="grid"><section><h2>Milestone criteria</h2><div class="row-list">${criteria}</div></section><section><h2>Go-live checklist</h2><div class="row-list">${checklist}</div></section></div>
<div class="grid"><section><h2>Money</h2>${facts([
      ["Spendable", usd(report.outcomes?.spendableMicros)],
      ["Customer receipts", usd(report.outcomes?.grossCustomerReceiptsMicros)],
      ["Net operating result", usd(report.outcomes?.netOperatingResultMicros)],
      ["After running costs", usd(report.resultAfterOutsideCostsMicros)],
      ["Owner top-ups", usd(report.outsideSupport?.ownerFundingMicros)],
      ["Interventions", String(report.interventions.total)],
    ])}</section><section><h2>Customer independence</h2><p>${
      attestation.status === "affirmed"
        ? `Attested ${escape(attestation.recordedAt ?? "")}. Evidence: ${escape(attestation.evidenceRef ?? "")}`
        : attestation.status === "withdrawn"
          ? "The latest attestation was withdrawn."
          : "Not attested. Confirm the paying customer is not you or acting for you, then record it."
    }</p>${form(
      "attest",
      `<input type="hidden" name="stance" value="affirmed">${field("Private evidence reference", "evidence", 'required maxlength="500"')}${field("Note (optional)", "note", 'maxlength="1000"')}`,
      "Attest independence",
    )}${
      attestation.status === "affirmed"
        ? form(
            "attest",
            `<input type="hidden" name="stance" value="withdrawn">${field("Reason", "evidence", 'required maxlength="500"')}`,
            "Withdraw attestation",
            true,
          )
        : ""
    }</section></div>
<section><h2>Costs outside the ledger</h2><p class="muted">Hosting, domains, subscriptions and tooling not paid from WideMind's balance. Entries can't be edited; correct one with a reducing entry.</p>${costs}<div class="grid"><div>${form(
      "cost",
      `<label>Category<select name="category">${COST_CATEGORIES.map((item) => `<option>${item}</option>`).join("")}</select></label>${field("Amount (USD)", "amount", 'required inputmode="decimal" placeholder="6.50"')}${field("From", "from", 'type="date" required')}${field("To", "to", 'type="date" required')}${field("Evidence (invoice, receipt)", "evidence", 'required maxlength="500"')}${field("Note (optional)", "note", 'maxlength="1000"')}`,
      "Record cost",
    )}</div><div>${form(
      "cost",
      `${field("Entry id to correct", "corrects", 'required inputmode="numeric"')}${field("Reduce by (USD)", "amount", 'required inputmode="decimal"')}${field("Evidence (credit note, error)", "evidence", 'required maxlength="500"')}`,
      "Record correction",
      true,
    )}</div></div></section>
<section><h2>Snapshots</h2><p class="muted">Only published snapshots appear on the public pilot page. Record chain ${data.chain.ok ? "verifies" : `<strong>does not verify</strong> (broken at ${data.chain.brokenAt})`} across ${data.chain.snapshots} snapshot(s).</p>${form(
      "snapshot",
      `<label><span><input type="checkbox" name="publish" value="true"> Publish on the public pilot page</span></label>`,
      "Take snapshot",
    )}${snapshots}</section>`,
  );
}
