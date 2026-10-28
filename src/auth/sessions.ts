import { createHmac, randomBytes } from "node:crypto";
import type { DatabaseSync } from "node:sqlite";
import type { IncomingMessage } from "node:http";
export interface Session {
  tokenHash: string;
  csrf: string;
  expiresAt: number;
}
export class Sessions {
  readonly db: DatabaseSync;
  private readonly credentialKey: string;
  constructor(db: DatabaseSync, credentialKey: string) {
    this.db = db;
    this.credentialKey = credentialKey;
  }
  private hash(value: string) {
    return createHmac("sha256", this.credentialKey).update(value).digest("hex");
  }
  create(now = Date.now()) {
    const token = randomBytes(32).toString("hex"),
      csrf = randomBytes(32).toString("hex");
    this.db.prepare("DELETE FROM auth_sessions WHERE expires_at<=?").run(now);
    this.db
      .prepare("INSERT INTO auth_sessions VALUES (?,?,?,?)")
      .run(this.hash(token), csrf, now + 8 * 3600000, now);
    return { token, csrf };
  }
  get(request: IncomingMessage, now = Date.now()): Session | null {
    const token = String(request.headers.cookie ?? "")
      .split(";")
      .map((v) => v.trim())
      .find((v) => v.startsWith("widemind_session="))
      ?.slice("widemind_session=".length);
    if (!token || !/^[a-f0-9]{64}$/.test(token)) return null;
    const row = this.db
      .prepare(
        "SELECT * FROM auth_sessions WHERE token_hash=? AND expires_at>?",
      )
      .get(this.hash(token), now);
    return row
      ? {
          tokenHash: String(row.token_hash),
          csrf: String(row.csrf),
          expiresAt: Number(row.expires_at),
        }
      : null;
  }
  revoke(session: Session) {
    this.db
      .prepare("DELETE FROM auth_sessions WHERE token_hash=?")
      .run(session.tokenHash);
  }
  allowAttempt(ip: string, now = Date.now()) {
    this.db
      .prepare("DELETE FROM auth_attempts WHERE window_at<?")
      .run(now - 900000);
    this.db
      .prepare(
        "INSERT INTO auth_attempts VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1",
      )
      .run(ip, now);
    return (
      Number(
        this.db.prepare("SELECT count FROM auth_attempts WHERE key=?").get(ip)
          ?.count,
      ) <= 10
    );
  }
}
