/** Reproducible offline qualification. No deployed provider/profile is selected. */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

if (process.argv.length !== 3 || process.argv[2] !== "--fixtures") {
  console.error("Usage: node scripts/business-demo.mjs --fixtures");
  console.error(
    "Explicit synthetic provider/customer/payment fixtures only; no real sale or delivery is performed.",
  );
  process.exitCode = 2;
} else {
  console.log(
    "WideMind business demonstration: explicitly offline fixtures, not real customers, payments, revenue or profit.",
  );
  const child = spawn(
    process.execPath,
    ["--test", "--test-concurrency=1", "test/business-integration.test.ts"],
    {
      cwd: fileURLToPath(new URL("../", import.meta.url)),
      env: { ...process.env, WIDEMIND_EXPLICIT_BUSINESS_DEMO: "1" },
      stdio: "inherit",
    },
  );
  child.on("error", (error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
  child.on("exit", (code, signal) => {
    process.exitCode = code ?? (signal ? 1 : 0);
  });
  for (const signal of ["SIGTERM", "SIGINT"])
    process.once(signal, () => child.kill(signal));
}
