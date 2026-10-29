/** One trusted scheduled wakeup; never reads mail, spends, funds, or resumes a run. */
import { parseArgs } from "node:util";
import { createHash } from "node:crypto";
import { bootstrap } from "../../bootstrap.ts";
import { Store } from "../../runtime/store.ts";

async function main() {
  const { values } = parseArgs({
    options: {
      "run-id": { type: "string" },
      "request-id": { type: "string" },
      "interval-seconds": { type: "string", default: "300" },
    },
    strict: true,
    allowPositionals: false,
  });
  const runId = values["run-id"];
  const interval = values["interval-seconds"]!;
  if (
    process.env.WIDEMIND_BUSINESS_ENABLED !== "true" ||
    typeof runId !== "string" ||
    !/^[a-f0-9-]{36}$/.test(runId) ||
    !/^[1-9][0-9]{0,5}$/.test(interval) ||
    Number(interval) < 60 ||
    Number(interval) > 86400
  )
    throw new Error("Invalid trusted inbox schedule configuration");
  const requestId =
    values["request-id"] ??
    `scheduled-inbox:${Math.floor(Date.now() / (Number(interval) * 1000))}:${interval}`;
  if (
    !requestId.trim() ||
    requestId.length > 200 ||
    /[\x00-\x1f\x7f]/.test(requestId)
  )
    throw new Error("Invalid inbox request identity");
  const context = await bootstrap();
  try {
    const store = new Store(context.db);
    const run = store.run(runId);
    if (!run || run.profile !== "foundation")
      throw new Error("An existing foundation run is required");
    const result = store.deliverEvent(runId, {
      source: "operator",
      accountRef: "operator",
      externalEventId: `business:${createHash("sha256")
        .update(JSON.stringify([runId, requestId]))
        .digest("hex")}`,
      kind: "business.command",
      payload: { command: "sync-inbox" },
    });
    console.log(
      JSON.stringify({
        queued: true,
        eventId: result.eventId,
        duplicate: result.duplicate,
        runtimeState: run.state,
      }),
    );
  } finally {
    context.db.close();
  }
}

main().catch(() => {
  console.error(
    "Inbox wakeup was not queued. Check the enabled foundation profile, existing --run-id, request identity, and private deployment configuration.",
  );
  process.exitCode = 1;
});
