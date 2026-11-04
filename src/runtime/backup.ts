/**
 * Verified SQLite backups for the runtime, economy, business and tools
 * databases. Copies use SQLite's online backup API, so running services and
 * WAL files are handled correctly. A backup directory is complete only once
 * its manifest exists; an interrupted run leaves a `.partial` directory that
 * the next run removes. Backups never modify the source databases.
 */
import { createHash } from "node:crypto";
import {
  chmodSync,
  closeSync,
  openSync,
  readSync,
  constants,
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, isAbsolute, join, relative, resolve } from "node:path";
import { backup as sqliteBackup, DatabaseSync } from "node:sqlite";
import { verifyChain } from "./pilot-snapshots.ts";

const STAMP = /^\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d(?:-\d{3})?Z$/;
const LABEL = /^[a-z0-9][a-z0-9._-]{0,99}$/;

export interface BackupSource {
  label: string;
  path: string;
}
export interface BackupSettings {
  dir: string;
  keep: number;
  maxAgeMs: number;
}
export interface ManifestFile {
  label: string;
  file: string;
  sourceName: string;
  bytes: number;
  sha256: string;
  userVersion: number;
  tables: number;
}
export interface Manifest {
  version: 1;
  createdAt: string;
  files: ManifestFile[];
}

export function backupSettings(env: NodeJS.ProcessEnv): BackupSettings | null {
  const dir = env.WIDEMIND_BACKUP_DIR;
  if (!dir) return null;
  if (!isAbsolute(dir))
    throw new Error("WIDEMIND_BACKUP_DIR must be an absolute path");
  const keep = env.WIDEMIND_BACKUP_KEEP ?? "14";
  const hours = env.WIDEMIND_BACKUP_MAX_AGE_HOURS ?? "26";
  if (!/^[0-9]{1,4}$/.test(keep) || Number(keep) < 1)
    throw new Error("WIDEMIND_BACKUP_KEEP must be an integer of at least 1");
  if (!/^[0-9]{1,5}$/.test(hours) || Number(hours) < 1)
    throw new Error(
      "WIDEMIND_BACKUP_MAX_AGE_HOURS must be an integer of at least 1",
    );
  return {
    dir: resolve(dir),
    keep: Number(keep),
    maxAgeMs: Number(hours) * 3_600_000,
  };
}

/**
 * The databases to back up. The runtime database is always included; the
 * others are included when their setting is present, and must then exist.
 */
export function backupSources(
  env: NodeJS.ProcessEnv,
  runtimeDatabase: string,
): BackupSource[] {
  const sources: BackupSource[] = [{ label: "runtime", path: runtimeDatabase }];
  if (env.WIDEMIND_ECONOMY_DB)
    sources.push({ label: "economy", path: resolve(env.WIDEMIND_ECONOMY_DB) });
  if (env.WIDEMIND_BUSINESS_DB)
    sources.push({
      label: "business",
      path: resolve(env.WIDEMIND_BUSINESS_DB),
    });
  if (env.WIDEMIND_TOOLS_STATE && existsSync(env.WIDEMIND_TOOLS_STATE)) {
    const root = resolve(env.WIDEMIND_TOOLS_STATE);
    const files = readdirSync(root, { recursive: true, withFileTypes: true })
      .filter((entry) => entry.isFile() && /\.(sqlite3?|db)$/.test(entry.name))
      .map((entry) => join(entry.parentPath, entry.name))
      .filter((file) => relative(root, file).split(/[\\/]/).length <= 3)
      .sort();
    for (const file of files)
      sources.push({
        label:
          "tools-" +
          relative(root, file)
            .replace(/[\\/]/g, "_")
            .replace(/[^a-z0-9._-]/gi, "-")
            .toLowerCase(),
        path: file,
      });
  }
  for (const item of (env.WIDEMIND_BACKUP_EXTRA ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean)) {
    const [label, path] = item.split("=", 2);
    if (!label || !path || !LABEL.test(label) || !isAbsolute(path))
      throw new Error(
        "WIDEMIND_BACKUP_EXTRA entries must be label=/absolute/path",
      );
    sources.push({ label, path });
  }
  const labels = new Set<string>();
  for (const source of sources) {
    if (!LABEL.test(source.label) || labels.has(source.label))
      throw new Error(`Invalid or duplicate backup label ${source.label}`);
    labels.add(source.label);
  }
  return sources;
}

/** Streamed in 1 MiB chunks so large databases never sit whole in memory. */
function sha256(path: string): string {
  const hash = createHash("sha256");
  const chunk = Buffer.alloc(1024 * 1024);
  const fd = openSync(path, "r");
  try {
    let read: number;
    while ((read = readSync(fd, chunk, 0, chunk.length, null)) > 0)
      hash.update(chunk.subarray(0, read));
  } finally {
    closeSync(fd);
  }
  return hash.digest("hex");
}

/** Integrity check of a copy; throws unless SQLite reports it intact. */
function inspect(path: string): { userVersion: number; tables: number } {
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    const rows = db.prepare("PRAGMA integrity_check").all() as {
      integrity_check: string;
    }[];
    if (rows.length !== 1 || rows[0]!.integrity_check !== "ok")
      throw new Error("integrity check failed");
    const version = db.prepare("PRAGMA user_version").get() as {
      user_version: number;
    };
    const tables = db
      .prepare("SELECT COUNT(*) AS n FROM sqlite_schema WHERE type='table'")
      .get() as { n: number };
    return {
      userVersion: Number(version.user_version),
      tables: Number(tables.n),
    };
  } finally {
    db.close();
  }
}

export function stamp(now: number): string {
  return new Date(now).toISOString().replace(/:/g, "-").replace(".", "-");
}

function completeBackups(dir: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter(
      (name) =>
        STAMP.test(name) && existsSync(join(dir, name, "manifest.json")),
    )
    .sort();
}

export async function runBackup(input: {
  settings: BackupSettings;
  sources: BackupSource[];
  now?: number;
}): Promise<{ path: string; manifest: Manifest; removed: string[] }> {
  const { settings, sources } = input;
  const now = input.now ?? Date.now();
  mkdirSync(settings.dir, { recursive: true, mode: 0o700 });
  // An earlier interrupted run never became a backup.
  for (const name of readdirSync(settings.dir))
    if (name.endsWith(".partial"))
      rmSync(join(settings.dir, name), { recursive: true, force: true });
  const name = stamp(now);
  const final = join(settings.dir, name);
  if (existsSync(final)) throw new Error("A backup with this time exists");
  const partial = final + ".partial";
  mkdirSync(partial, { mode: 0o700 });
  try {
    const files: ManifestFile[] = [];
    for (const source of sources) {
      if (!existsSync(source.path))
        throw new Error(`Backup source ${source.label} does not exist`);
      const file = source.label + ".sqlite";
      const target = join(partial, file);
      const db = new DatabaseSync(source.path, { readOnly: true });
      try {
        db.exec("PRAGMA busy_timeout=10000");
        await sqliteBackup(db, target);
      } finally {
        db.close();
      }
      let checked;
      try {
        checked = inspect(target);
      } catch (error) {
        throw new Error(
          `Backup copy of ${source.label} failed verification: ${(error as Error).message}`,
        );
      }
      files.push({
        label: source.label,
        file,
        sourceName: basename(source.path),
        bytes: statSync(target).size,
        sha256: sha256(target),
        ...checked,
      });
    }
    const manifest: Manifest = {
      version: 1,
      createdAt: new Date(now).toISOString(),
      files,
    };
    writeFileSync(
      join(partial, "manifest.json"),
      JSON.stringify(manifest, null, 2) + "\n",
      { mode: 0o600 },
    );
    renameSync(partial, final);
    // A timestamp-only marker so unprivileged monitors can check freshness
    // without read access to the backup copies themselves.
    const marker = join(settings.dir, "latest.json");
    writeFileSync(
      marker + ".tmp",
      JSON.stringify({ createdAt: manifest.createdAt, backup: name }) + "\n",
    );
    chmodSync(marker + ".tmp", 0o644);
    renameSync(marker + ".tmp", marker);
    const complete = completeBackups(settings.dir);
    const removed = complete.slice(
      0,
      Math.max(0, complete.length - settings.keep),
    );
    for (const old of removed)
      rmSync(join(settings.dir, old), { recursive: true, force: true });
    return { path: final, manifest, removed };
  } catch (error) {
    rmSync(partial, { recursive: true, force: true });
    throw error;
  }
}

export function latestBackup(
  dir: string,
): { path: string; createdAt: string } | null {
  const name = completeBackups(dir).at(-1);
  if (!name) return null;
  try {
    const manifest = JSON.parse(
      readFileSync(join(dir, name, "manifest.json"), "utf8"),
    ) as Manifest;
    if (!Number.isFinite(Date.parse(manifest.createdAt))) return null;
    return { path: join(dir, name), createdAt: manifest.createdAt };
  } catch {
    return null;
  }
}

/** Age check used by alerts and preflight; null when backups are not configured. */
export function backupFresh(
  settings: BackupSettings | null,
  now = Date.now(),
): { fresh: boolean; detail: string } | null {
  if (!settings) return null;
  let latest: { createdAt: string } | null = null;
  try {
    const marker = JSON.parse(
      readFileSync(join(settings.dir, "latest.json"), "utf8"),
    ) as { createdAt?: unknown };
    if (
      typeof marker.createdAt === "string" &&
      Number.isFinite(Date.parse(marker.createdAt))
    )
      latest = { createdAt: marker.createdAt };
  } catch {
    try {
      latest = latestBackup(settings.dir);
    } catch {
      return { fresh: false, detail: "Backup directory is not readable" };
    }
  }
  if (!latest) return { fresh: false, detail: "No complete backup exists yet" };
  const age = now - Date.parse(latest.createdAt);
  const hours = Math.floor(age / 3_600_000);
  return age <= settings.maxAgeMs
    ? { fresh: true, detail: `Latest backup is ${hours} hour(s) old` }
    : {
        fresh: false,
        detail: `Latest backup is ${hours} hour(s) old, over the ${settings.maxAgeMs / 3_600_000} hour limit`,
      };
}

export interface BackupVerification {
  ok: boolean;
  path: string;
  createdAt: string | null;
  files: { label: string; ok: boolean; detail: string }[];
  snapshotChains: { runs: number; ok: boolean } | null;
}

/**
 * Restore drill without touching live data: re-hash every copy, re-run the
 * integrity check, and verify each run's snapshot hash chain in the runtime copy.
 */
export function verifyBackup(path: string): BackupVerification {
  const result: BackupVerification = {
    ok: false,
    path,
    createdAt: null,
    files: [],
    snapshotChains: null,
  };
  let manifest: Manifest;
  try {
    manifest = JSON.parse(readFileSync(join(path, "manifest.json"), "utf8"));
  } catch {
    result.files.push({
      label: "manifest",
      ok: false,
      detail: "manifest.json missing or unreadable",
    });
    return result;
  }
  result.createdAt = manifest.createdAt;
  for (const item of manifest.files) {
    const file = join(path, basename(item.file));
    if (!existsSync(file)) {
      result.files.push({
        label: item.label,
        ok: false,
        detail: "file missing",
      });
      continue;
    }
    if (sha256(file) !== item.sha256) {
      result.files.push({
        label: item.label,
        ok: false,
        detail: "checksum does not match the manifest",
      });
      continue;
    }
    try {
      inspect(file);
      result.files.push({ label: item.label, ok: true, detail: "intact" });
    } catch (error) {
      result.files.push({
        label: item.label,
        ok: false,
        detail: (error as Error).message,
      });
    }
  }
  const runtime = manifest.files.find((item) => item.label === "runtime");
  const runtimeOk = result.files.find((item) => item.label === "runtime")?.ok;
  if (runtime && runtimeOk) {
    const db = new DatabaseSync(join(path, basename(runtime.file)), {
      readOnly: true,
    });
    try {
      const hasTable = db
        .prepare(
          "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='pilot_snapshots'",
        )
        .get();
      const runs = hasTable
        ? (
            db.prepare("SELECT DISTINCT run_id FROM pilot_snapshots").all() as {
              run_id: string;
            }[]
          ).map((row) => row.run_id)
        : [];
      result.snapshotChains = {
        runs: runs.length,
        ok: runs.every((run) => verifyChain(db, run).ok),
      };
    } finally {
      db.close();
    }
  }
  result.ok =
    result.files.length === manifest.files.length &&
    result.files.every((item) => item.ok) &&
    (result.snapshotChains?.ok ?? true);
  return result;
}

/** Copy one verified database out of a backup to a new path; never overwrites. */
export function restoreFile(input: {
  backupPath: string;
  label: string;
  to: string;
}): { to: string; sha256: string } {
  const check = verifyBackup(input.backupPath);
  const entry = check.files.find((item) => item.label === input.label);
  if (!entry) throw new Error(`Backup has no ${input.label} database`);
  if (!entry.ok)
    throw new Error(
      `Backup copy of ${input.label} is not intact: ${entry.detail}`,
    );
  const target = resolve(input.to);
  for (const suffix of ["", "-wal", "-shm"])
    if (existsSync(target + suffix))
      throw new Error(
        `${target + suffix} exists; restore to a new path and swap it in while services are stopped`,
      );
  copyFileSync(
    join(input.backupPath, input.label + ".sqlite"),
    target,
    constants.COPYFILE_EXCL,
  );
  return { to: target, sha256: sha256(target) };
}
