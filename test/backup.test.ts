import test from "node:test";
import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import {
  backupFresh,
  backupSettings,
  backupSources,
  latestBackup,
  restoreFile,
  runBackup,
  verifyBackup,
} from "../src/runtime/backup.ts";
import { takeSnapshot } from "../src/runtime/pilot-snapshots.ts";
import { alertPass } from "../src/runtime/alerts.ts";
import { fixture } from "./helpers.ts";

function sqlite(path: string, rows: number) {
  const db = new DatabaseSync(path);
  db.exec(
    "PRAGMA journal_mode=WAL; CREATE TABLE ledger(id INTEGER PRIMARY KEY, amount TEXT)",
  );
  for (let i = 0; i < rows; i++)
    db.prepare("INSERT INTO ledger(amount) VALUES (?)").run(String(i));
  return db;
}

test("backup settings and sources are explicit and validated", () => {
  assert.equal(backupSettings({}), null);
  assert.throws(
    () => backupSettings({ WIDEMIND_BACKUP_DIR: "relative" }),
    /absolute/,
  );
  assert.throws(
    () =>
      backupSettings({ WIDEMIND_BACKUP_DIR: "/b", WIDEMIND_BACKUP_KEEP: "0" }),
    /KEEP/,
  );
  assert.deepEqual(backupSettings({ WIDEMIND_BACKUP_DIR: "/b" }), {
    dir: "/b",
    keep: 14,
    maxAgeMs: 26 * 3_600_000,
  });
  const f = fixture();
  try {
    const tools = join(f.dir, "tools");
    mkdirSync(join(tools, "nested"), { recursive: true });
    writeFileSync(join(tools, "control.sqlite3"), "");
    writeFileSync(join(tools, "nested", "actions.sqlite3"), "");
    writeFileSync(join(tools, "notes.txt"), "");
    const sources = backupSources(
      {
        WIDEMIND_ECONOMY_DB: "/e.sqlite",
        WIDEMIND_TOOLS_STATE: tools,
        WIDEMIND_BACKUP_EXTRA: "journal=/j.sqlite",
      },
      "/r.sqlite",
    );
    assert.deepEqual(
      sources.map((item) => item.label),
      [
        "runtime",
        "economy",
        "tools-control.sqlite3",
        "tools-nested_actions.sqlite3",
        "journal",
      ],
    );
    assert.throws(
      () => backupSources({ WIDEMIND_BACKUP_EXTRA: "runtime=/x" }, "/r"),
      /duplicate/,
    );
    assert.throws(
      () => backupSources({ WIDEMIND_BACKUP_EXTRA: "x=relative" }, "/r"),
      /absolute/,
    );
  } finally {
    f.close();
  }
});

test("backups copy live WAL databases, verify, rotate and restore without overwriting", async () => {
  const f = fixture();
  try {
    const economyPath = join(f.dir, "economy.sqlite");
    const economy = sqlite(economyPath, 50);
    // Uncommitted writes stay out; committed WAL content is included.
    economy.exec("BEGIN; INSERT INTO ledger(amount) VALUES ('uncommitted')");
    const id = f.store.create("demo");
    f.services.economy.health = () => ({ available: true });
    f.services.economy.summary = async () => null;
    await takeSnapshot({
      db: f.db,
      services: f.services,
      runId: id,
      publish: true,
    });
    const settings = {
      dir: join(f.dir, "backups"),
      keep: 2,
      maxAgeMs: 3_600_000,
    };
    const sources = [
      { label: "runtime", path: f.config.database },
      { label: "economy", path: economyPath },
    ];
    let t = Date.parse("2026-11-04T10:00:00Z");
    const first = await runBackup({ settings, sources, now: t });
    economy.exec("ROLLBACK");
    assert.deepEqual(
      first.manifest.files.map((item) => item.label),
      ["runtime", "economy"],
    );
    const copy = new DatabaseSync(join(first.path, "economy.sqlite"), {
      readOnly: true,
    });
    assert.equal(
      (copy.prepare("SELECT COUNT(*) AS n FROM ledger").get() as { n: number })
        .n,
      50,
    );
    copy.close();
    assert.equal(verifyBackup(first.path).ok, true);
    assert.deepEqual(verifyBackup(first.path).snapshotChains, {
      runs: 1,
      ok: true,
    });

    await runBackup({ settings, sources, now: (t += 60_000) });
    const third = await runBackup({ settings, sources, now: (t += 60_000) });
    assert.equal(third.removed.length, 1);
    assert.equal(existsSync(first.path), false);
    assert.equal(
      readdirSync(settings.dir).filter((name) => name !== "latest.json").length,
      2,
    );
    assert.equal(latestBackup(settings.dir)?.path, third.path);
    const marker = JSON.parse(
      readFileSync(join(settings.dir, "latest.json"), "utf8"),
    );
    assert.equal(marker.createdAt, third.manifest.createdAt);
    assert.equal(
      statSync(join(settings.dir, "latest.json")).mode & 0o777,
      0o644,
    );

    // Freshness drives the alert and preflight.
    assert.equal(backupFresh(settings, t + 1000)?.fresh, true);
    assert.equal(backupFresh(settings, t + 2 * 3_600_000)?.fresh, false);
    assert.equal(
      backupFresh({ ...settings, dir: join(f.dir, "none") }, t)?.fresh,
      false,
    );
    const result = await alertPass({
      db: f.db,
      services: f.services,
      config: f.config,
      runId: id,
      settings: {
        webhookUrl: null,
        repeatMs: 3_600_000,
        economy: null,
        backup: settings,
      },
      now: t + 2 * 3_600_000,
    });
    assert.ok(result.active.includes("backup_stale"));

    // Restore copies to a new path only, after verifying the copy.
    const restored = join(f.dir, "restored.sqlite");
    const out = restoreFile({
      backupPath: third.path,
      label: "economy",
      to: restored,
    });
    assert.equal(out.sha256.length, 64);
    assert.throws(
      () =>
        restoreFile({ backupPath: third.path, label: "economy", to: restored }),
      /exists/,
    );
    assert.throws(
      () =>
        restoreFile({
          backupPath: third.path,
          label: "economy",
          to: economyPath,
        }),
      /exists/,
    );
    assert.throws(
      () =>
        restoreFile({
          backupPath: third.path,
          label: "missing",
          to: join(f.dir, "x"),
        }),
      /no missing/,
    );

    // Damage to a copy or to the snapshot chain is detected.
    const damaged = join(third.path, "economy.sqlite");
    const bytes = readFileSync(damaged);
    bytes[bytes.length - 100] = bytes[bytes.length - 100]! ^ 0xff;
    writeFileSync(damaged, bytes);
    const bad = verifyBackup(third.path);
    assert.equal(bad.ok, false);
    assert.match(
      bad.files.find((item) => item.label === "economy")!.detail,
      /checksum/,
    );
    assert.throws(
      () =>
        restoreFile({
          backupPath: third.path,
          label: "economy",
          to: join(f.dir, "y"),
        }),
      /not intact/,
    );
    economy.close();
  } finally {
    f.close();
  }
});

test("a missing configured source fails the backup and leaves no partial backup", async () => {
  const f = fixture();
  try {
    const settings = {
      dir: join(f.dir, "backups"),
      keep: 3,
      maxAgeMs: 3_600_000,
    };
    await assert.rejects(
      runBackup({
        settings,
        sources: [
          { label: "runtime", path: f.config.database },
          { label: "economy", path: join(f.dir, "missing.sqlite") },
        ],
      }),
      /economy does not exist/,
    );
    assert.deepEqual(readdirSync(settings.dir), []);
    assert.equal(latestBackup(settings.dir), null);
  } finally {
    f.close();
  }
});
