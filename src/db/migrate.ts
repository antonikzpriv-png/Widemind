import { createHash } from "node:crypto";
import type { DatabaseSync } from "node:sqlite";
import type { Migration, ModuleRegistration } from "../contracts/index.ts";
import { transaction } from "./database.ts";
import { coreMigrations } from "./migrations/core.ts";
export function migrate(
  db: DatabaseSync,
  modules: readonly ModuleRegistration[] = [],
) {
  db.exec(
    "CREATE TABLE IF NOT EXISTS schema_migrations (module TEXT NOT NULL, version INTEGER NOT NULL, checksum TEXT NOT NULL, applied_at INTEGER NOT NULL, PRIMARY KEY(module,version))",
  );
  const sources = [{ id: "core", migrations: coreMigrations }, ...modules];
  for (const source of sources) {
    if (!/^[a-z][a-z0-9_-]*$/.test(source.id))
      throw new Error("Invalid migration namespace");
    for (const item of source.migrations ?? []) apply(db, source.id, item);
  }
}
function apply(db: DatabaseSync, module: string, migration: Migration) {
  if (!Number.isSafeInteger(migration.version) || migration.version < 1)
    throw new Error("Invalid migration version");
  const checksum = createHash("sha256").update(migration.sql).digest("hex");
  transaction(db, () => {
    const existing = db
      .prepare(
        "SELECT checksum FROM schema_migrations WHERE module=? AND version=?",
      )
      .get(module, migration.version);
    if (existing) {
      if (existing.checksum !== checksum)
        throw new Error(
          `Applied migration changed: ${module}/${migration.version}`,
        );
      return;
    }
    const latest =
      db
        .prepare(
          "SELECT MAX(version) AS version FROM schema_migrations WHERE module=?",
        )
        .get(module)?.version ?? 0;
    if (Number(latest) >= migration.version)
      throw new Error("Migrations must be appended in increasing order");
    db.exec(migration.sql);
    db.prepare("INSERT INTO schema_migrations VALUES (?,?,?,?)").run(
      module,
      migration.version,
      checksum,
      Date.now(),
    );
  });
}
