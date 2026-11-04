// Backups: `backup` (default), `backup verify [--path <dir>]`,
// `backup restore --label <name> --to <new path> [--path <dir>]`.
// Exit status 2 when verification fails.
import { loadConfig } from "../config.ts";
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
} else throw new Error("Operation must be run, verify or restore");
