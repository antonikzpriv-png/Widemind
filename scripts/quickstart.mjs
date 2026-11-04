/**
 * One command for a new owner: install what's missing, configure the agent's
 * identity and service tokens, create an owner password (printed once, only
 * its hash is stored), migrate, and start everything. Safe to re-run: every
 * step is skipped when already done. Pass --no-start to stop before launching.
 */
import { spawnSync, spawn } from "node:child_process";
import { existsSync, readFileSync, appendFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { join } from "node:path";

const root = process.cwd();
const major = Number(process.versions.node.split(".")[0]);
if (major !== 24) {
  console.error(
    `WideMind needs Node.js 24 (this is ${process.versions.node}). With nvm: nvm install && nvm use`,
  );
  process.exit(1);
}
const step = (label) => console.log(`\n→ ${label}`);
const run = (command, args) => {
  const result = spawnSync(command, args, { stdio: "inherit", cwd: root });
  if (result.status !== 0) {
    console.error(
      `\nStopped: ${command} ${args.join(" ")} failed. Fix the error above and run npm run quickstart again.`,
    );
    process.exit(result.status ?? 1);
  }
};
const envText = () =>
  [".env", "var/integration.env"]
    .map((file) =>
      existsSync(join(root, file))
        ? readFileSync(join(root, file), "utf8")
        : "",
    )
    .join("\n") +
  "\n" +
  (process.env.WIDEMIND_OPERATOR_PASSWORD_HASH
    ? "WIDEMIND_OPERATOR_PASSWORD_HASH=set\n"
    : "");

if (!existsSync(join(root, "node_modules"))) {
  step("Installing Node packages");
  run("npm", ["ci"]);
}
if (
  !["economy", "tools", "business"].every((name) =>
    existsSync(join(root, ".venvs", name)),
  )
) {
  step(
    "Installing the economy, tools and business services (about 30 seconds)",
  );
  run(process.execPath, ["scripts/services.mjs", "setup"]);
}
if (!existsSync(join(root, "var/integration.env"))) {
  step("Creating your agent's identity and private service tokens");
  run(process.execPath, ["scripts/services.mjs", "configure"]);
}
let password = null;
if (!/^WIDEMIND_OPERATOR_PASSWORD_HASH=./m.test(envText())) {
  step("Creating your owner password");
  const { hashPassword } = await import("../src/auth/password.ts");
  // 24 characters in readable groups, about 120 bits.
  const alphabet = "abcdefghjkmnpqrstuvwxyz23456789";
  const bytes = randomBytes(24);
  password = Array.from(bytes, (byte) => alphabet[byte % alphabet.length])
    .join("")
    .replace(/(.{6})(?=.)/g, "$1-");
  appendFileSync(
    join(root, "var/integration.env"),
    `WIDEMIND_OPERATOR_PASSWORD_HASH=${await hashPassword(password)}\n`,
    { mode: 0o600 },
  );
}
step("Preparing the database");
run("npm", ["run", "-s", "migrate"]);

const port = process.env.WIDEMIND_PORT ?? "3000";
console.log(
  `\n✓ WideMind is ready.\n\n  Sign in:  http://127.0.0.1:${port}/operator`,
);
if (password)
  console.log(
    `  Password: ${password}\n\n  Save this password now; it is shown only once. Only its hash is stored (in var/integration.env).`,
  );
else console.log("  Password: the one you set earlier.");

if (process.argv.includes("--no-start")) {
  console.log("\nStart everything later with: npm run dev:all");
} else {
  console.log(
    "\nStarting the services, website and worker. Press Ctrl-C to stop.\n",
  );
  const child = spawn("npm", ["run", "-s", "dev:all"], {
    stdio: "inherit",
    cwd: root,
  });
  for (const signal of ["SIGINT", "SIGTERM"])
    process.on(signal, () => child.kill(signal));
  child.on("exit", (code) => process.exit(code ?? 0));
}
