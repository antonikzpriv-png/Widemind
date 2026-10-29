import { loadConfig } from "./config.ts";
import { openDatabase } from "./db/database.ts";
import { discoverModules, registerModules } from "./modules/registry.ts";
export async function bootstrap(env: NodeJS.ProcessEnv = process.env) {
  process.umask(0o077);
  const { config, secrets } = loadConfig(env);
  const db = openDatabase(config.database);
  try {
    return {
      config,
      secrets,
      db,
      services: registerModules(
        { db, config, env },
        await discoverModules(config),
      ),
    };
  } catch (error) {
    db.close();
    throw error;
  }
}
