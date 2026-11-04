// Backups: `backup` (default), `backup verify [--path <dir>]`,
// `backup restore --label <name> --to <new path> [--path <dir>]`.
// Exit status 2 when verification fails.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { loadConfig } from "../config.ts";
import {
  decryptArchive,
  generateBackupKeys,
  offsiteSettings,
  uploadBackup,
} from "./offsite.ts";
import { option, required } from "./cli-args.ts";
import {
  backupSettings,
  backupSources,
  latestBackup,
  restoreFile,
  runBackup,
  verifyBackup,
} from "./backup.ts";
const [operation = "run", ...args] = process.argv.slice(2);
// These two run on the operator's own machine and need no backup settings.
if (operation === "keygen") {
  const privatePath = required(args, "--private"),
    publicPath = required(args, "--public");
  for (const path of [privatePath, publicPath])
    if (existsSync(path))
      throw new Error(`${path} exists; refusing to overwrite`);
  const keys = generateBackupKeys();
  writeFileSync(privatePath, keys.privateKeyPem, { mode: 0o600, flag: "wx" });
  writeFileSync(publicPath, keys.publicKeyPem, { mode: 0o644, flag: "wx" });
  console.log(
    `Private key written to ${privatePath}: keep it off the server, with a second copy somewhere safe. Without it the off-host backups cannot be opened.\nPublic key written to ${publicPath}: copy it to the server and set WIDEMIND_OFFSITE_PUBLIC_KEY.`,
  );
  process.exit(0);
}
if (operation === "decrypt") {
  const out = required(args, "--out");
  const names = decryptArchive(
    readFileSync(required(args, "--in")),
    readFileSync(required(args, "--key"), "utf8"),
    out,
  );
  const check = verifyBackup(out);
  console.log(
    JSON.stringify({ out, files: names, verification: check }, null, 2),
  );
  if (!check.ok) process.exitCode = 2;
  process.exit();
}
const settings = backupSettings(process.env);
if (!settings) throw new Error("Set WIDEMIND_BACKUP_DIR to an absolute path");
const chosen = () => {
  const path = option(args, "--path") ?? latestBackup(settings.dir)?.path;
  if (!path) throw new Error("No complete backup found");
  return path;
};
if (operation === "run") {
  const { config } = loadConfig(process.env);
  const result = await runBackup({
    settings,
    sources: backupSources(process.env, config.database),
  });
  console.log(
    JSON.stringify(
      {
        path: result.path,
        createdAt: result.manifest.createdAt,
        files: result.manifest.files.map(({ label, bytes, sha256 }) => ({
          label,
          bytes,
          sha256,
        })),
        removed: result.removed,
      },
      null,
      2,
    ),
  );
} else if (operation === "verify") {
  const result = verifyBackup(chosen());
  console.log(JSON.stringify(result, null, 2));
  if (!result.ok) process.exitCode = 2;
} else if (operation === "restore") {
  console.log(
    JSON.stringify(
      restoreFile({
        backupPath: chosen(),
        label: required(args, "--label"),
        to: required(args, "--to"),
      }),
      null,
      2,
    ),
  );
} else if (operation === "offsite") {
  const offsite = offsiteSettings(process.env);
  if (!offsite) {
    console.log(
      "Off-host backups are not configured (WIDEMIND_OFFSITE_*); skipped.",
    );
  } else {
    const marker = await uploadBackup({
      settings: offsite,
      backup: settings,
      backupPath: chosen(),
    });
    console.log(JSON.stringify(marker, null, 2));
  }
} else
  throw new Error(
    "Operation must be run, verify, restore, offsite, keygen or decrypt",
  );
